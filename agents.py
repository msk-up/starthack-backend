
from botocore import client
from pydantic import BaseModel
import json


def call_bedrock(prompt: str, system_prompt: str = "") -> str:
    """Call Amazon Bedrock gpt-oss-120b model and return response text."""
    messages = [{"role": "user", "content": prompt}] if system_prompt:
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


class NegotiationAgent(BaseModel):
    sys_prompt: str
    insights:str
    product: str
    def __init__(self,client, sys_prompt: str, insights:str, product: str) -> None:
        self.sys_prompt = sys_prompt
        self.insights = insights
        self.product = product
        self.client = client
        return
    def send_message(self)-> str:

        try :
            response = self.client.invoke_model(
                 modelId="openai.gpt-oss-120b-1:0",
                 contentType="application/json",
                 accept="application/json",
                 body=json.dumps(body),
            )
        except Exception as e:
            return f"Bedrock service is currently unavailable. {e}"
        result = json.loads(response["body"].read())
        return result["choices"][0]["message"]["content"]







