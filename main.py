import asyncio
import json
import os
import uuid
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

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

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("negotiation")

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
If you see during your anaylsis that one of the suppliers has made a final offer. Mark the negotiation as complete;
"""

bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

pool: asyncpg.Pool | None = None
# --- Initialize Email Client ---
email_client = EmailClient()
email_router = EmailEventRouter()
active_sessions: dict[str, NegotiationSession] = {}

EMAIL_ADDRESS = os.environ.get("EMAIL_USER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")


async def email_watcher():
    """Background task that watches for incoming emails and routes them."""
    import re

    logger.info("Starting email watcher task...")
    logger.info(f"Email client logged in: {email_client.email_address is not None}")
    logger.info(f"Active sessions count: {len(active_sessions)}")

    try:
        logger.info("Starting email_trigger generator...")
        async for email_data in email_client.email_trigger():
            logger.info("=" * 50)
            logger.info("EMAIL WATCHER: Received email from email_trigger")
            logger.info(
                f"New email received from: {email_data['sender']}, subject: {email_data['subject']}"
            )

            # Try to find matching supplier by email
            db = await get_pool()
            sender_email = email_data["sender"]

            # Extract email from "Name <email@domain.com>" format if needed
            email_match = re.search(r"<([^>]+)>", sender_email)
            if email_match:
                sender_email = email_match.group(1)

            supplier_row = await db.fetchrow(
                "SELECT supplier_id FROM supplier WHERE supplier_email = $1",
                sender_email,
            )

            if not supplier_row:
                logger.warning(f"No supplier found for email: {email_data['sender']}")
                continue

            # Try to extract ng_id and sup_id from subject line [REF-xxxxxxxx-yyyyyyyy]
            ng_id = None
            supplier_id = None
            subject = email_data["subject"]
            ref_match = re.search(
                r"\[REF-([a-f0-9]{8})-([a-f0-9]{8})\]", subject, re.IGNORECASE
            )

            if ref_match:
                ng_prefix = ref_match.group(1)
                sup_prefix = ref_match.group(2)
                logger.info(
                    f"Found reference in subject: ng={ng_prefix}, sup={sup_prefix}"
                )

                # Find the full ng_id that starts with this prefix
                for session_ng_id in active_sessions.keys():
                    if session_ng_id.startswith(ng_prefix):
                        ng_id = session_ng_id
                        break

                # Also check DB if not in active sessions
                if not ng_id:
                    ng_row = await db.fetchrow(
                        "SELECT ng_id FROM negotiation WHERE CAST(ng_id AS TEXT) LIKE $1",
                        f"{ng_prefix}%",
                    )
                    if ng_row:
                        ng_id = str(ng_row["ng_id"])

                # Find the full supplier_id that starts with this prefix
                sup_row = await db.fetchrow(
                    "SELECT supplier_id FROM supplier WHERE CAST(supplier_id AS TEXT) LIKE $1",
                    f"{sup_prefix}%",
                )
                if sup_row:
                    supplier_id = str(sup_row["supplier_id"])
                    logger.info(f"Matched supplier from subject: {supplier_id}")

            # Fallback: try to match by sender email if no REF tag
            if not supplier_id and supplier_row:
                supplier_id = str(supplier_row["supplier_id"])
                logger.info(f"Matched supplier by email: {supplier_id}")

            # Fallback: find any active negotiation for this supplier
            if not ng_id and supplier_id:
                logger.info("No ng_id in subject, searching active sessions...")
                for session_ng_id, session in active_sessions.items():
                    if supplier_id in session._agents:
                        ng_id = session_ng_id
                        break

            if not ng_id:
                logger.warning(
                    f"No active negotiation found for supplier: {supplier_id}"
                )
                continue

            logger.info(
                f"Routing email to negotiation: {ng_id}, supplier: {supplier_id}"
            )

            # Create event and push to router
            from router import EmailEvent

            event = EmailEvent(
                sender=email_data["sender"],
                subject=email_data["subject"],
                body=email_data["body"],
                supplier_id=supplier_id,
                ng_id=ng_id,
                raw=email_data,
            )
            logger.info(f"Pushing event to email_router...")
            await email_router.push(event)
            logger.info(f"Event pushed successfully")
            logger.info("=" * 50)

    except asyncio.CancelledError:
        logger.info("Email watcher cancelled")
    except Exception as e:
        logger.error(f"Email watcher error: {e}", exc_info=True)


email_watcher_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool, email_watcher_task
    logger.info("Starting application...")
    pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)
    logger.info("Database pool created")

    # Login email client if credentials are provided
    if EMAIL_ADDRESS and EMAIL_PASSWORD:
        try:
            logger.info(f"Logging in email client as {EMAIL_ADDRESS}...")
            await email_client.email_login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            logger.info("Email client logged in successfully")

            # Start email watcher background task
            email_watcher_task = asyncio.create_task(email_watcher())
            logger.info("Email watcher started")
        except Exception as e:
            logger.error(f"Email login failed: {e}")
    else:
        logger.warning(
            "No email credentials provided - email sending/receiving disabled"
        )

    yield

    logger.info("Shutting down...")
    if email_watcher_task:
        email_watcher_task.cancel()
        try:
            await email_watcher_task
        except asyncio.CancelledError:
            pass
        logger.info("Email watcher stopped")
    if pool:
        await pool.close()
        logger.info("Database pool closed")


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
    logger.info(f"Starting negotiation for product: {request.product}")
    logger.info(f"Suppliers: {request.suppliers}")
    logger.info(f"Tactics: {request.tactics}")

    db = await get_pool()

    ng_id = str(uuid.uuid4())
    logger.info(f"Created negotiation ID: {ng_id}")

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
    logger.info("Negotiation saved to database")

    orchestrator = OrchestratorAgent(
        client=bedrock_client,
        strategy=request.tactics,
        product=request.product,
        sys_promt=OCHESTRATOR_AGENT_SYSTEM_PROMPT,
        db_pool=db,
        ng_id=ng_id,
    )
    logger.info("Orchestrator agent created")

    # Create a session to manage this negotiation
    session = NegotiationSession(
        db_pool=db,
        client=bedrock_client,
        ng_id=ng_id,
        orchestrator=orchestrator,
        router=email_router,
    )
    logger.info("Negotiation session created")

    for supplier in request.suppliers:
        logger.info(f"Processing supplier: {supplier}")

        # Fetch supplier info from DB
        supplier_row = await db.fetchrow(
            "SELECT supplier_name, supplier_email, description, insights FROM supplier WHERE supplier_id = $1",
            supplier,
        )
        if not supplier_row:
            logger.warning(f"Supplier {supplier} not found in database, skipping")
            continue

        supplier_name = supplier_row["supplier_name"] or "Supplier"
        supplier_email = supplier_row["supplier_email"]
        supplier_description = supplier_row["description"] or ""
        supplier_insights = supplier_row["insights"] or ""

        logger.info(f"Supplier: {supplier_name}, email: {supplier_email or 'NOT SET'}")

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
        logger.info(f"Agent saved to database for supplier {supplier}")

        agent = NegotiationAgent(
            db_pool=db,
            sys_prompt=NEGOTIATOR_AGENT_SYSTEM_PROMPT,
            ng_id=ng_id,
            sup_id=supplier,
            client=bedrock_client,
            product=request.product,
            email_client=email_client,
            supplier_email=supplier_email,
            supplier_name=supplier_name,
            supplier_insights=supplier_insights,
        )
        logger.info(f"NegotiationAgent created for supplier {supplier}")

        # Register agent with session - this sets up the email handler
        session.add_agent(supplier, agent)
        logger.info(f"Agent registered with session for supplier {supplier}")

        # Send initial message to supplier asking about offers
        logger.info(f"Sending initial message to supplier {supplier}...")
        reply = await agent.send_initial_message(context=request.prompt)
        logger.info(f"Initial message sent to supplier {supplier}")
        logger.debug(
            f"Message content: {reply[:100]}..."
            if len(reply) > 100
            else f"Message content: {reply}"
        )

    # Store session for later reference
    active_sessions[ng_id] = session
    logger.info(
        f"Negotiation {ng_id} started successfully with {len(request.suppliers)} suppliers"
    )

    return {
        "negotiation_id": ng_id,
        "status": "started",
        "suppliers": request.suppliers,
    }


@app.get("/conversation/{negotiation_id}/{supplier_id}")
async def get_conversation(negotiation_id: str, supplier_id: str) -> dict[str, Any]:
    db = await get_pool()
    messages = await db.fetch(
        "SELECT * FROM message WHERE ng_id = $1 AND supplier_id = $2",
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
            "SELECT * FROM message WHERE ng_id = $1 AND supplier_id = $2 ORDER BY message_timestamp DESC",
            negotiation_id,
            row["sup_id"],
        )

        # Check if any message in this conversation is marked as completed
        is_completed = any(msg.get("completed", False) for msg in messages)

        # Get supplier name
        supplier = await db.fetchrow(
            "SELECT supplier_name FROM supplier WHERE supplier_id = $1",
            row["sup_id"],
        )

        response.append(
            {
                "supplier_id": str(row["sup_id"]),
                "supplier_name": supplier["supplier_name"] if supplier else None,
                "message_count": len(messages),
                "completed": is_completed,
            }
        )

    # Check if all supplier negotiations are completed
    all_completed = all(agent["completed"] for agent in response) if response else False

    return {
        "negotiation_id": negotiation_id,
        "all_completed": all_completed,
        "agents": response,
    }


@app.get("/orchestrator_activity/{negotiation_id}")
async def get_orchestrator_activity(
    negotiation_id: str, supplier_id: Optional[str] = None
) -> dict[str, Any]:
    db = await get_pool()
    params: list[Any] = [negotiation_id]
    query = """
        SELECT oa.activity_id,
               oa.ng_id,
               oa.supplier_id,
               oa.activity_timestamp,
               oa.action,
               oa.summary,
               oa.details,
               oa.completed,
               s.supplier_name
        FROM orchestrator_activity oa
        LEFT JOIN supplier s ON oa.supplier_id = s.supplier_id
        WHERE oa.ng_id = $1
    """
    if supplier_id:
        query += " AND oa.supplier_id = $2"
        params.append(supplier_id)
    query += " ORDER BY oa.activity_timestamp DESC"

    rows = await db.fetch(query, *params)
    activities = []
    for row in rows:
        activities.append(
            {
                "activity_id": str(row["activity_id"]),
                "supplier_id": str(row["supplier_id"]) if row["supplier_id"] else None,
                "supplier_name": row["supplier_name"],
                "action": row["action"],
                "summary": row["summary"],
                "details": row["details"],
                "completed": row["completed"],
                "timestamp": row["activity_timestamp"].isoformat()
                if row["activity_timestamp"]
                else None,
            }
        )

    return {
        "negotiation_id": negotiation_id,
        "count": len(activities),
        "activities": activities,
    }


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
