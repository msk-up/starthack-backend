import json
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import asyncpg
import boto3
from fastapi import FastAPI
from pydantic import BaseModel

DATABASE_URL = os.environ["DB_URL"]
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

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


class NegotiationRequest(BaseModel):
    product: int
    prompt: str
    tactics: str
    suppliers: list[str]


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


@app.get("/test")
async def test_bedrock() -> dict[str, Any]:
    """Test Bedrock integration with a simple prompt."""
    prompt = "Explain the benefits of using Amazon Bedrock for AI applications."
    response_text = call_bedrock(prompt)
    return {"response": response_text}


@app.post("/negotiations")
async def trigger_negotiations(request: NegotiationRequest) -> dict[str, Any]:
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
