import os
import json
import logging
import boto3
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from google import genai
from google.genai import types

from app.core.config import settings

logger = logging.getLogger("orchestra-ai-worker")

# Module-level cache so we only fetch from AWS SSM once per Lambda container lifespan
_gemini_api_key_cache = None

def get_gemini_api_key() -> str:
    """Securely fetches the API key from SSM with in-memory caching."""
    global _gemini_api_key_cache
    if _gemini_api_key_cache:
        return _gemini_api_key_cache
        
    # Local development fallback
    env_key = os.getenv("GEMINI_API_KEY")
    if env_key:
        _gemini_api_key_cache = env_key
        return env_key
        
    # Production AWS SSM Fetch
    try:
        logger.info("Fetching Gemini API Key from AWS SSM Parameter Store...")
        ssm = boto3.client('ssm', region_name=settings.AWS_REGION)
        param_name = f"/orchestra-ai/{settings.ENVIRONMENT}/gemini_api_key"
        
        response = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _gemini_api_key_cache = response['Parameter']['Value']
        return _gemini_api_key_cache
    except Exception as e:
        logger.error(f"Failed to fetch Gemini API key from SSM: {str(e)}")
        raise ValueError("AI API Key is not configured in SSM.")

class RateLimitException(Exception):
    """Raised when an external API throttles our request"""
    pass

class AIProvider(ABC):
    """
    Abstract Base Class for AI Providers.
    Contractor Signal: Dependency Inversion. We depend on this interface, 
    not the specific vendor SDKs, allowing easy addition of Claude/Mistral later.
    """
    @abstractmethod
    def process(self, text: str, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        pass

class GeminiProvider(AIProvider):
    def __init__(self):
        # Securely fetch the API key (uses cache after first invocation)
        self.api_key = get_gemini_api_key()
        
        # Initialize the new standard Google GenAI SDK client
        self.client = genai.Client(api_key=self.api_key)
        
    def process(self, text: str, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        logger.info("Calling Gemini API")
        
        # Pricing per 1M tokens (Gemini 2.0 Flash approximation)
        # Input: $0.10 / 1M | Output: $0.40 / 1M
        INPUT_PRICE_PER_TOKEN = 0.10 / 1_000_000
        OUTPUT_PRICE_PER_TOKEN = 0.40 / 1_000_000
        
        # The prompt strategy
        prompt = "Analyze the following text."
        if schema:
            prompt += f"\nExtract the data strictly matching this JSON schema:\n{json.dumps(schema)}"
        prompt += f"\n\nText:\n{text}"

        try:
            # We use gemini-2.5-flash as the default fast/cost-effective model
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json" if schema else "text/plain",
                )
            )
            
            # Extract usage metadata
            # Note: The new SDK structure puts usage_metadata on the response object
            input_tokens = 0
            output_tokens = 0
            
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                input_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
                output_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
            
            cost = (input_tokens * INPUT_PRICE_PER_TOKEN) + (output_tokens * OUTPUT_PRICE_PER_TOKEN)
            
            # Parse result (if schema was provided, it should be JSON)
            result_text = response.text
            extracted_data = result_text
            if schema:
                try:
                    # Clean markdown code blocks if the model wrapped the JSON
                    if result_text.startswith("```json"):
                        result_text = result_text.replace("```json\n", "").replace("```", "")
                    extracted_data = json.loads(result_text)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse JSON from Gemini response")
                    extracted_data = {"raw_text": result_text}

            return {
                "result": extracted_data,
                "metrics": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": round(cost, 6),
                    "model_used": "gemini-2.5-flash"
                }
            }
            
        except Exception as e:
            error_str = str(e).lower()
            # Catch 429 Resource Exhausted / Quota Exceeded
            if "429" in error_str or "quota" in error_str or "exhausted" in error_str:
                raise RateLimitException(f"Gemini Rate Limit Exceeded: {str(e)}")
            raise e

class OpenAIProvider(AIProvider):
    """
    Placeholder for OpenAI implementation.
    Demonstrates how easily we can add providers to the factory.
    """
    def process(self, text: str, schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raise NotImplementedError("OpenAI provider not fully configured yet.")

class AIServiceFactory:
    """Factory to get the right provider based on the requested profile"""
    @staticmethod
    def get_provider(model_profile: str) -> AIProvider:
        # We map profiles to specific vendor models.
        # fast-cost-effective -> Gemini 2.5 Flash
        # ultra-premium -> Could map to OpenAI GPT-4o or Gemini 2.5 Pro later
        
        if model_profile == "fast-cost-effective":
            return GeminiProvider()
        elif model_profile == "ultra-premium":
            # For now, default to Gemini if OpenAI isn't wired up
            logger.info("Ultra-premium requested, defaulting to Gemini for now")
            return GeminiProvider()
        else:
            return GeminiProvider()
