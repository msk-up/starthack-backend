import json
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

from agents import NegotiationAgent

load_dotenv()

import asyncpg
import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DATABASE_URL = os.environ["DB_URL"]
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")
FRONTEND_ORIGINS = os.environ.get("FRONTEND_ORIGINS", "")

bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

pool: asyncpg.Pool | None = None


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



async def crate_negotiation_agent(supplier_id: str,tactics: str, product: str)h->str:

    db = await get_pool()

    row = await db.fetch("SELECT * FROM supplier WHERE supplier_name = $1 LIMIT 1", supplier_id)
    insights = row[0]['insights']
    prompt = f'''

    


    '''







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
        insights = insights_row[0]['insights']
        agent  = NegotiationAgent("",insights, request.product)








    
    return {"status": "not implemented"}


@app.get("/suppliers")
async def suppliers() -> dict[str, Any]:
    db = await get_pool()
    rows = await db.fetch("SELECT * FROM supplier")
    suppliers = [dict(row) for row in rows]
    return {"suppliers": suppliers}


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
