import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel
from fastapi import HTTPException, FastAPI
from fastapi.middleware.cors import CORSMiddleware
import asyncpg
import boto3

# Local imports
from email_client import EmailClient
from agents import NegotiationAgent, OrchestratorAgent
from router import EmailEventRouter, NegotiationSession

load_dotenv()

DATABASE_URL = os.environ["DB_URL"]
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")
FRONTEND_ORIGINS = os.environ.get("FRONTEND_ORIGINS", "")

NEGOTIATOR_AGENT_SYSTEM_PROMPT = """
You are a skilled negotation agent representing a buyer in a procurment process. Your goal is to win the best possible deal for the
the company. While your are negotiating an Supervisor agent is monetoring your progress and giving you new 
instructions every new step of the negotiation. Follow their instructions carefully and adapt your strategy accordingly
Further instructions might be provided following this. Make sure to follow them closely.
"""

OCHESTRATOR_AGENT_SYSTEM_PROMPT = """
Your are a negotiationg orchestration agent. The company you are are working for is looking to procure a product. Your goal is to 
be a consultant to other agents each responsible for one particular supplier of that product.
You will have to follow the main instructions given to you and when asked to reflect them also in the advice you 
give to the other agents.
Make sure to gain understanding of the overall negotiation progress and give strategic advice to the other agents when asked.
You might want to give them information about the progress of other agents as well as additional instructions. Use this to guide
their behavior and if requested by the user make smart decisions on how to reduce to overall price of the product through clever
negotiation tactics advice to the other agents, which might include the recommendation to present the supplier with a  
competing offer from another supplier that your agents are alo negotiating with.
"""

bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

pool: asyncpg.Pool | None = None
# --- Initialize Email Client ---
email_client = EmailClient()
email_router = EmailEventRouter()
active_sessions: dict[str, NegotiationSession] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)
    yield
    if pool:
        await pool.close()


app = FastAPI(title="Health API", version="0.1.0", lifespan=lifespan)

allowed_origins = [
    origin.strip() for origin in FRONTEND_ORIGINS.split(",") if origin.strip()
] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def get_pool() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    return pool


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/suppliers")
async def list_suppliers() -> list[dict[str, Any]]:
    db = await get_pool()
    rows = await db.fetch("SELECT * FROM supplier")
    return [dict(row) for row in rows]


@app.get("/products")
async def list_products() -> list[dict[str, Any]]:
    db = await get_pool()
    rows = await db.fetch("SELECT * FROM product")
    return [dict(row) for row in rows]


@app.get("/search")
async def search_items(product: str) -> list[dict[str, Any]]:
    db = await get_pool()
    rows = await db.fetch(
        "SELECT * FROM product WHERE product_name ILIKE $1", f"%{product}%"
    )
    return [dict(row) for row in rows]


def call_bedrock(prompt: str, system_prompt: str = "") -> str:
    """Call Amazon Bedrock gpt-oss-120b model and return response text."""
    messages = [{"role": "user", "content": prompt}]
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})

    body = {
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.7,
    }

    try:
        response = bedrock_client.invoke_model(
            modelId="openai.gpt-oss-120b-1:0",
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
    except Exception as e:
        return f"Bedrock service is currently unavailable. {e}"

    result = json.loads(response["body"].read())
    return result["choices"][0]["message"]["content"]


# FIXED SYNTAX ERROR HERE
async def crate_negotiation_agent(supplier_id: str, tactics: str, product: str) -> str:
    db = await get_pool()
    row = await db.fetch(
        "SELECT * FROM supplier WHERE supplier_name = $1 LIMIT 1", supplier_id
    )
    if not row:
        return ""
    insights = row[0]["insights"]
    prompt = f"""
    Negotiate for {product} with tactics {tactics}. Insights: {insights}
    """
    return prompt


# --- NEW EMAIL ENDPOINTS ---


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/email/login")
async def email_login_endpoint(creds: LoginRequest):
    """
    Exposed endpoint for frontend to log in the email client.
    """
    try:
        await email_client.email_login(creds.email, creds.password)
        return {"status": "success", "message": "Logged in successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class SendEmailRequest(BaseModel):
    to_email: str
    subject: str
    body: str


@app.post("/email/send")
async def email_send_endpoint(req: SendEmailRequest):
    """
    Exposed endpoint to send emails using logged in credentials.
    """
    try:
        await email_client.email_send(req.to_email, req.subject, req.body)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------


class NegotiationRequest(BaseModel):
    product: str
    prompt: str
    tactics: str
    suppliers: list[str]


@app.post("/negotiate")
async def trigger_negotiations(request: NegotiationRequest) -> dict[str, Any]:
    db = await get_pool()

    ng_id = str(uuid.uuid4())

    # Save negotiation to DB
    await db.execute(
        """
        INSERT INTO negotiation (ng_id, product, strategy, status)
        VALUES ($1, $2, $3, 'active')
        """,
        ng_id,
        request.product,
        request.tactics,
    )

    orchestrator = OrchestratorAgent(
        client=bedrock_client,
        strategy=request.tactics,
        product=request.product,
        sys_promt=OCHESTRATOR_AGENT_SYSTEM_PROMPT,
        db_pool=db,
        ng_id=ng_id,
    )

    # Create a session to manage this negotiation
    session = NegotiationSession(
        db_pool=db,
        client=bedrock_client,
        ng_id=ng_id,
        orchestrator=orchestrator,
        router=email_router,
    )

    for supplier in request.suppliers:
        # Save negotiator agent to DB
        await db.execute(
            """
            INSERT INTO agent (ng_id, sup_id, sys_prompt, role)
            VALUES ($1, $2, $3, 'negotiator')
            """,
            ng_id,
            supplier,
            NEGOTIATOR_AGENT_SYSTEM_PROMPT,
        )

        agent = NegotiationAgent(
            db_pool=db,
            sys_prompt=NEGOTIATOR_AGENT_SYSTEM_PROMPT,
            ng_id=ng_id,
            sup_id=supplier,
            client=bedrock_client,
            product=request.product,
        )
        # Register agent with session - this sets up the email handler
        session.add_agent(supplier, agent)

    # Store session for later reference
    active_sessions[ng_id] = session

    return {
        "negotiation_id": ng_id,
        "status": "started",
        "suppliers": request.suppliers,
    }


@app.get("/conversation/{negotiation_id}/{supplier_id}")
async def get_conversation(negotiation_id: str, supplier_id: str) -> dict[str, Any]:
    db = await get_pool()
    messages = await db.fetch(
        "SELECT * FROM message WHERE negotiation_id = $1 AND supplier_id = $2",
        negotiation_id,
        supplier_id,
    )
    return {"message": [dict(message) for message in messages]}


@app.get("/negotiation_status/{negotiation_id}")
async def negotiation_status(negotiation_id: str) -> dict[str, Any]:
    db = await get_pool()
    rows = await db.fetch("SELECT * FROM agent WHERE ng_id = $1", negotiation_id)

    response = []
    for row in rows:
        messages = await db.fetch(
            "SELECT * FROM message WHERE ng_id = $1 AND supplier_id = $2",
            negotiation_id,
            row["sup_id"],
        )
        response.append(
            {
                "supplier_id": str(row["sup_id"]),
                "message_count": len(messages),
            }
        )

    return {"negotiation_id": negotiation_id, "agents": response}


@app.get("/get_negotations")
async def get_negotations() -> dict[str, Any]:
    db = await get_pool()
    rows = await db.fetch("SELECT * FROM negotiation")

    response = []
    for row in rows:
        response.append(
            {
                "negotiation_id": str(row["ng_id"]),
                "product": row["product"],
                "strategy": row["strategy"],
                "status": row["status"],
            }
        )

    return {"negotiations": response}


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
