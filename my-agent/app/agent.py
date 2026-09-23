# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.models import Gemini
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.tools.tool_context import ToolContext
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback


MODEL = "gemini-3.8-flash"
PROJECT_ID = "qwiklabs-gcp-01-cec5bf0618af"
BUCKET_NAME = "smart-pantry-assets-qwiklabs-gcp-01-cec5bf0618af"
AGENT_ENGINE_RESOURCE_NAME = (
    f"projects/{PROJECT_ID}/locations/us-east1/reasoningEngines/8553367034283425792"
)
SANDBOX_RESOURCE_NAME = (
    f"{AGENT_ENGINE_RESOURCE_NAME}/sandboxEnvironments/2871977148309045248"
)


def get_firestore_client() -> firestore.Client:
    return firestore.Client(project=PROJECT_ID)


def get_storage_client() -> storage.Client:
    return storage.Client(project=PROJECT_ID)


def list_pantry_items(category: str = "") -> str:
    """Lists items in the pantry database.

    Args:
        category: Optional filter for a specific category (e.g., 'Dairy', 'Produce', 'Grains').

    Returns:
        A formatted string listing the items in the pantry.
    """
    db = get_firestore_client()
    query = db.collection("pantry_items")
    if category:
        query = query.where("category", "==", category)
    docs = query.stream()
    items = [doc.to_dict() for doc in docs]
    if not items:
        return "No pantry items found."
    lines = []
    for item in items:
        lines.append(
            f"- {item.get('name')} ({item.get('quantity')} {item.get('unit')}, Category: {item.get('category')}, Expires: {item.get('expiration_date')})"
        )
    return "Pantry Items:\n" + "\n".join(lines)


def add_pantry_item(
    name: str, category: str, quantity: float, unit: str, expiration_date: str
) -> str:
    """Adds a new item to the pantry database in Firestore.

    Args:
        name: Name of the item (e.g. 'Avocados').
        category: Category of the item (e.g. 'Produce', 'Dairy', 'Grains').
        quantity: Quantity count or weight.
        unit: Measurement unit (e.g. 'pieces', 'kg', 'carton').
        expiration_date: Expiration date string in YYYY-MM-DD format.

    Returns:
        A confirmation string with details of the added item.
    """
    db = get_firestore_client()
    doc_id = f"item-{int(datetime.datetime.now().timestamp())}"
    item_data = {
        "id": doc_id,
        "name": name,
        "category": category,
        "quantity": quantity,
        "unit": unit,
        "expiration_date": expiration_date,
    }
    db.collection("pantry_items").document(doc_id).set(item_data)
    return f"Successfully added {name} ({quantity} {unit}) to pantry items."


def search_recipes(ingredient: str) -> str:
    """Searches for real recipes containing a specific main ingredient using TheMealDB API.

    Args:
        ingredient: The main ingredient to search recipes for (e.g., 'chicken', 'basil', 'rice', 'cheese').

    Returns:
        A formatted list of matching recipe names and thumbnail URLs, or a status message if none found.
    """
    encoded = urllib.parse.quote(ingredient.strip().lower())
    url = f"https://www.themealdb.com/api/json/v1/1/filter.php?i={encoded}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            meals = data.get("meals")
            if not meals:
                return f"No recipes found containing '{ingredient}'."
            results = []
            for meal in meals[:5]:
                results.append(f"- {meal.get('strMeal')} (Image: {meal.get('strMealThumb')})")
            return f"Recipes containing '{ingredient}':\n" + "\n".join(results)
    except Exception as e:
        return f"Error searching recipes for '{ingredient}': {e}"


def get_food_nutrition_facts(food_name: str) -> str:
    """Fetches real nutritional facts and calories for a food item from USDA FoodData Central.

    Args:
        food_name: The name of the food item to look up (e.g. 'whole milk', 'olive oil', 'basil', 'rice').

    Returns:
        A summary string containing calories, protein, fats, and carbs per serving/100g.
    """
    api_key = os.environ.get("USDA_FDC_API_KEY", "DEMO_KEY")
    encoded = urllib.parse.quote(food_name.strip())
    url = f"https://api.nal.usda.gov/fdc/v1/foods/search?api_key={api_key}&query={encoded}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            foods = data.get("foods", [])
            if not foods:
                return f"No nutritional data found for '{food_name}'."
            best_match = foods[0]
            desc = best_match.get("description", food_name.title())
            nutrients = best_match.get("foodNutrients", [])
            nutrient_lines = []
            for n in nutrients[:8]:
                name = n.get("nutrientName")
                val = n.get("value")
                unit = n.get("unitName", "")
                if name and val is not None:
                    nutrient_lines.append(f"- {name}: {val} {unit}")
            return f"Nutritional facts for {desc}:\n" + "\n".join(nutrient_lines)
    except Exception as e:
        return f"Error fetching nutrition facts for '{food_name}': {e}"


async def generate_pantry_item_image(
    prompt: str,
    tool_context: ToolContext,
) -> str:
    """Generates an image for a pantry item or dish using gemini-3.1-flash-lite-image in the global region.

    Saves the image as a Playground artifact and uploads the image bytes directly to public Cloud Storage.

    Args:
        prompt: Detailed visual description of the food item or dish (e.g. 'A bowl of fresh basil and tomato pasta').
        tool_context: ToolContext for saving the image artifact in Playground.

    Returns:
        The public HTTPS URL (https://storage.googleapis.com/<bucket>/<object>) of the generated image.
    """
    client = genai.Client(
        vertexai=True,
        location="global",
        project=PROJECT_ID,
    )
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-image",
        contents=f"High quality photo of: {prompt}",
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )

    part = response.candidates[0].content.parts[0]
    image_bytes = part.inline_data.data
    mime_type = part.inline_data.mime_type or "image/jpeg"
    ext = "png" if "png" in mime_type else "jpg"
    filename = f"pantry-{int(datetime.datetime.now().timestamp())}.{ext}"

    # 1. Save artifact in Playground
    artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    await tool_context.save_artifact(filename, artifact_part)

    # 2. Upload image bytes directly to public GCS bucket
    storage_client = get_storage_client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(image_bytes, content_type=mime_type)

    public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
    return public_url


async def generate_pantry_item_video(
    prompt: str,
    tool_context: ToolContext,
) -> str:
    """Generates a short video for a pantry item or dish using gemini-omni-flash-preview in the global region.

    Saves the video as a Playground artifact and uploads the video bytes directly to public Cloud Storage.

    Args:
        prompt: Detailed description of the video content (e.g. 'A short video showing fresh basil leaves').
        tool_context: ToolContext for saving the video artifact in Playground.

    Returns:
        The public HTTPS URL (https://storage.googleapis.com/<bucket>/<object>) of the generated video.
    """
    client = genai.Client(
        vertexai=True,
        location="global",
        project=PROJECT_ID,
    )
    response = client.models.generate_content(
        model="gemini-omni-flash-preview",
        contents=f"Generate a short video showing: {prompt}",
        config=types.GenerateContentConfig(response_modalities=["VIDEO"]),
    )

    part = response.candidates[0].content.parts[0]
    video_bytes = part.inline_data.data
    mime_type = part.inline_data.mime_type or "video/mp4"
    filename = f"pantry-video-{int(datetime.datetime.now().timestamp())}.mp4"

    # 1. Save artifact in Playground
    artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
    await tool_context.save_artifact(filename, artifact_part)

    # 2. Upload video bytes directly to public GCS bucket
    storage_client = get_storage_client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(video_bytes, content_type=mime_type)

    public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
    return public_url


def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        city: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


async def generate_memories_callback(callback_context: CallbackContext):
    try:
        await callback_context.add_session_to_memory()
    except Exception:
        pass
    return None


code_executor = AgentEngineSandboxCodeExecutor(
    sandbox_resource_name=SANDBOX_RESOURCE_NAME,
    agent_engine_resource_name=AGENT_ENGINE_RESOURCE_NAME,
)

schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description="You are a helpful smart pantry & kitchen AI assistant. Use list_pantry_items and add_pantry_item tools to inspect and update the user's pantry collection. Use search_recipes to find real meal ideas. Use get_food_nutrition_facts to look up real nutrition info and calories. Use generate_pantry_item_image to create images for food items or dishes. Use generate_pantry_item_video to create short videos for food items or dishes. Use get_weather and get_current_time for weather or time questions. You also remember user dietary preferences and facts across sessions. You can also execute Python code in a safe Agent Engine sandbox environment when complex calculations, data analysis, or scripting is needed.",
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)

root_agent = Agent(
    # Keep in sync with agents-cli-manifest.yaml: agents-cli derives this name
    # from the project `name:` recorded there, and telemetry reports it as
    # gen_ai.agent.name. Renaming the agent only here makes the two disagree,
    # and anything selecting traces by name stops finding this agent's.
    name="my_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=code_executor,
    tools=[
        list_pantry_items,
        add_pantry_item,
        search_recipes,
        get_food_nutrition_facts,
        generate_pantry_item_image,
        generate_pantry_item_video,
        get_weather,
        get_current_time,
        PreloadMemoryTool(),
    ],
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)





