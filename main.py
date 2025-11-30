import json
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

from agents import NegotiationAgent
# --- NEW IMPORTS ---
from pydantic import BaseModel
from fastapi import HTTPException
from email_client import EmailClient

load_dotenv()

import asyncpg
import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE_URL = os.environ["DB_URL"]
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")
FRONTEND_ORIGINS = os.environ.get("FRONTEND_ORIGINS", "")

bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

pool: asyncpg.Pool | None = None
# --- Initialize Email Client ---
email_client = EmailClient()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)
    yield
    if pool:
        await pool.close()


app = FastAPI(title="Health API", version="0.1.0", lifespan=lifespan)

allowed_origins = [origin.strip() for origin in FRONTEND_ORIGINS.split(",") if origin.strip()] or ["*"]
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

    try :

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
async def crate_negotiation_agent(supplier_id: str,tactics: str, product: str) -> str:

    db = await get_pool()

    row = await db.fetch("SELECT * FROM supplier WHERE supplier_name = $1 LIMIT 1", supplier_id)
    if not row: return ""
    insights = row[0]['insights']
    prompt = f'''
    Negotiate for {product} with tactics {tactics}. Insights: {insights}
    '''
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

    for supplier in request.suppliers:
        insights_row = await db.fetch("SELECT * FROM supplier WHERE supplier_name = $1 LIMIT 1", supplier)
        if not insights_row: continue
        insights = insights_row[0]['insights']
        agent  = NegotiationAgent(pool, bedrock_client, "", insights, request.product) # Passed required args
        # agent.send_message()
        ## safe in db
        ## callback once a response from the oponent is found

    return {"status": "not implemented"}


# Removed duplicate @app.get("/suppliers") as it was already defined above

def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()