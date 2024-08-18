import asyncio
import random
import logging
import os
import re
from starlette.applications import Starlette
from starlette.routing import Route, Mount, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from starlette.staticfiles import StaticFiles
from starlette.responses import FileResponse, JSONResponse
from starlette.requests import Request
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware import Middleware
from dotenv import load_dotenv
import httpx
from httpx import AsyncClient, Timeout

# Load environment variables from .env file
load_dotenv()

from helpers import (
    get_random_temperature, construct_greetings_prompt, construct_topic_prompt,
    construct_context_prompt, construct_batch_100_dollar_prompt, parse_100_dollar_response,
    validate_dollar_distribution, enrich_stories_with_dollar_distribution,
    construct_stories_formatted, ensure_unique_keys, estimate_wsjf, estimate_moscow, 
    save_uploaded_file, parse_csv_to_json, estimate_kano, estimate_ahp, send_to_llm
)

from agent import (
    prioritize_stories_with_ahp, categorize_stories_with_moscow,
    generate_user_stories_with_epics,
    prioritize_stories_with_100_dollar_method, OPENAI_URL, check_stories_with_framework
)

LLAMA_URL="https://api.groq.com/openai/v1/chat/completions"

api_keys = [os.getenv(f"API-KEY{i}") for i in range(1, 4)]
llama_keys = [os.getenv(f"LLAMA-key{i}") for i in range(1, 3)]


print("API Keys:", api_keys)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            if "stories" in data and "prioritization_type" in data and "model" in data:
                stories = data.get("stories")
                model = data.get("model")
                prioritization_type = data.get("prioritization_type").upper()  # Normalize to uppercase
                await run_agents_workflow(stories, prioritization_type, model, websocket)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    finally:
        if websocket.application_state != WebSocketState.DISCONNECTED:
            await websocket.close()


async def run_agents_workflow(stories, prioritization_type, model, websocket):
   
    # Step 1: Greetings
    greetings_prompt = construct_greetings_prompt(prioritization_type)
    greetings_response = await engage_agents(greetings_prompt, websocket, "PO", model)

    try:
        # Log the raw response for debugging
        logger.info(f"Raw greetings response: {greetings_response}")

        # Improved parsing logic
        greetings = greetings_response.split("2. ")
        pekka_greeting = greetings[0].strip()
        remaining_greetings = greetings[1].split("3. ")
        sami_greeting = remaining_greetings[0].strip()
        zeeshan_greeting = remaining_greetings[1].strip()

        # Send greetings with delay
        await stream_response_word_by_word(websocket, pekka_greeting, "PO")
        await asyncio.sleep(1)  # Delay of 1 second between greetings
        
        await stream_response_word_by_word(websocket, sami_greeting, "QA")
        await asyncio.sleep(1)  # Delay of 1 second between greetings
        
        await stream_response_word_by_word(websocket, zeeshan_greeting, "developer")
    except Exception as e:
        logger.error(f"Error parsing greetings response: {greetings_response}")
        logger.error(f"Exception: {e}")
        await websocket.send_json({"agentType": "error", "message": "Error parsing greetings response. Please try again later."})

     # Step 2: Topic Introduction
    topic_prompt = construct_topic_prompt(stories, prioritization_type)
    logger.info(f"topic_prompt : {topic_prompt}")
    
    # Step 3: Context and Discussion
    context_prompt = construct_context_prompt(stories, prioritization_type)
    
    # Run Step 2 and Step 3 concurrently
    topic_response, context_response = await asyncio.gather(
        engage_agents(topic_prompt, websocket, "QA", model),
        engage_agents(context_prompt, websocket, "developer", model)
    )

    logger.info(f"topic_prompt response: {topic_response}")
    
    # Step 4: Prioritization
    if prioritization_type == "100_DOLLAR":
        # prioritize_prompt = construct_batch_100_dollar_prompt({"stories": stories}, topic_response, context_response)
        prioritize_prompt = construct_batch_100_dollar_prompt({"stories": stories}, topic_response, context_response )
        prioritized_stories = await engage_agents_in_prioritization(prioritize_prompt, stories, websocket, model)
        print("Final 100 dollar", prioritized_stories)
    elif prioritization_type == "WSJF":
        prioritized_stories = await estimate_wsjf(stories, websocket, model, topic_response, context_response)
    elif prioritization_type == "MOSCOW":
        prioritized_stories = await estimate_moscow(stories, websocket, model, topic_response, context_response)
    elif prioritization_type == "KANO":
        prioritized_stories = await estimate_kano(stories, websocket, model, topic_response, context_response)
    elif prioritization_type == "AHP":
        prioritized_stories = await estimate_ahp({"stories": stories}, websocket, model, topic_response, context_response)    
    else:
        raise ValueError(f"Unsupported prioritization type: {prioritization_type}")

    # Step 5: Final Output
    await stream_response_word_by_word(websocket, "Here is the final prioritized output:", "Final Prioritization")

    # Step 6: Final Output in table
    await asyncio.sleep(1)  # Delay of 1 second between greetings
    await websocket.send_json({"agentType": "Final_output_into_table", "message": prioritized_stories, "prioritization_type": prioritization_type})

async def engage_agents(prompt, websocket, agent_type, model, max_retries=1):
    headers = {
        "Authorization": f"Bearer {random.choice(api_keys)}",
        "Content-Type": "application/json"
    }

    agent_prompt = {"role": "user", "content": prompt}
    
    logger.info(f"Engaging agents with prompt: {prompt}")
    agent_response = await send_to_llm(agent_prompt['content'], headers, model)
    
    if agent_response:
        await stream_response_word_by_word(websocket, agent_response, agent_type)
    else:
        agent_response = "No response from agent"
    
    return agent_response

async def engage_agents_in_prioritization(prompt, stories, websocket, model, max_retries=1 ):
    headers = {
        "Authorization": f"Bearer {random.choice(api_keys)}",
        "Content-Type": "application/json"
    }

    logger.info(f"Engaging agents in prioritization with prompt: {prompt}")
    for attempt in range(max_retries):
        try:
            final_response = await send_to_llm(prompt, headers, model)
            logger.info(f"Final response from agent: {final_response}")  # Detailed logging
            await stream_response_word_by_word(websocket, final_response, "Final Prioritization")

            dollar_distribution = parse_100_dollar_response(final_response)

            if not dollar_distribution:
                logger.error(f"Failed to parse dollar distribution: {final_response}")
                continue

            logger.info(f"Dollar Response: {dollar_distribution}")
            
            enriched_stories = enrich_stories_with_dollar_distribution(stories, dollar_distribution)
            await websocket.send_json({"agentType": "Final Prioritization", "message": {"stories": enriched_stories}})
            return enriched_stories

            # if validate_dollar_distribution(dollar_distribution, stories):
            #     enriched_stories = enrich_stories_with_dollar_distribution(stories, dollar_distribution)
            #     await websocket.send_json({"agentType": "Final Prioritization", "message": {"stories": enriched_stories}})
            #     return enriched_stories
            # else:
            #     logger.error("Dollar distribution validation failed")

        except Exception as e:
            logger.error(f"Error during prioritization attempt {attempt + 1}: {str(e)}")
        logger.info(f"Retrying prioritization... ({attempt + 1}/{max_retries})")

    raise Exception("Failed to get valid response from agents after multiple attempts")

# async def send_to_llm(prompt, headers, model, max_retries=1):
#     for attempt in range(max_retries):
#         try:

#             if model.startswith("llama3") or model == "mixtral-8x7b-32768":
#                 url = LLAMA_URL
#                 headers["Authorization"] = f"Bearer {random.choice(llama_keys)}"
#             else:
#                 url = OPENAI_URL
#                 headers["Authorization"] = f"Bearer {random.choice(api_keys)}"
            

#             post_data = {
#                 "model": model,
#                 "messages": [
#                     {"role": "system", "content": "You are an expert in prioritization techniques."},
#                     {"role": "user", "content": prompt}
#                 ],
#                 "temperature": 0.7
#             }

#             timeout = Timeout(60.0, connect=60.0)
#             async with AsyncClient(timeout=timeout) as client:
#                 response = await client.post(url, json=post_data, headers=headers)

#                 if response.status_code == 200:
#                     completion = response.json()
#                     return completion['choices'][0]['message']['content']
#                 else:
#                     logger.error(f"Error: Received a non-200 status code: {response.status_code}")
#                     logger.error(f"Response text: {response.text}")
#         except httpx.RequestError as e:
#             logger.error(f"HTTP Request failed: {str(e)}")
#         logger.info(f"Retrying... ({attempt + 1}/{max_retries})")

#     raise Exception("Failed to get response from OpenAI after multiple attempts")


async def stream_response_word_by_word(websocket, response, agent_type, delay=0.6):
    
    if websocket.application_state != WebSocketState.DISCONNECTED:
        await websocket.send_json({
            "agentType": agent_type,
            "message": response
        })
        await asyncio.sleep(delay)  # Delay to simulate streaming effect  

async def stream_response_as_complete_message(websocket: WebSocket, response: str, agent_type: str, delay: float = 0.6):
    if websocket.application_state != WebSocketState.DISCONNECTED:
        await websocket.send_json({
            "agentType": agent_type,
            "message": response
        })
        await asyncio.sleep(delay)  # Delay to simulate streaming effect            

async def catch_all(request):
    return FileResponse(os.path.join('dist', 'index.html'))

async def generate_user_stories(request: Request):
    data = await request.json()
    headers = {
        "Authorization": f"Bearer {random.choice(api_keys)}",
        "Content-Type": "application/json"
    }

    if not data or 'objective' not in data or 'model' not in data:
        return JSONResponse({'error': 'Missing required data: objective, and model'}, status_code=400)
    model = data['model']
    objective = data['objective']
    stories_with_epics = generate_user_stories_with_epics(objective, model,headers)
    return JSONResponse({"stories_with_epics": stories_with_epics})


async def check_user_stories_quality(request: Request):
    data = await request.json()
    headers = {
        "Authorization": f"Bearer {random.choice(api_keys)}",
        "Content-Type": "application/json"
    }
    if not data or 'framework' not in data or 'stories' not in data or 'model' not in data:
        return JSONResponse({'error': 'Missing required data: framework, stories, and model'}, status_code=400)
    
    model = data['model']
    framework = data['framework']
    stories = data['stories']
    stories_with_epics = check_stories_with_framework(framework, stories, model, headers)
    return JSONResponse({"stories_with_epics": stories_with_epics})

async def upload_csv(request: Request):
    form = await request.form()
    file = form.get("file")
    if not file:
        return JSONResponse({'error': 'No file part'}, status_code=400)
    
    file_path, error = save_uploaded_file(UPLOAD_FOLDER, file)
    
    if error:
        return JSONResponse({'error': error}, status_code=400)
    if file_path:
        csv_data = parse_csv_to_json(file_path)
        return JSONResponse({"stories_with_epics": csv_data})

current_dir = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(current_dir, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Starlette(debug=True, middleware=[
    Middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
], routes=[
    Route('/api/generate-user-stories', generate_user_stories, methods=['POST']),
    Route('/api/upload-csv', upload_csv, methods=['POST']),
    Route('/api/check-user-stories-quality', check_user_stories_quality, methods=['POST']),
    WebSocketRoute("/api/ws-chat", websocket_endpoint),
    Mount('/', StaticFiles(directory='dist', html=True), name='static')
])




if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app,host='0.0.0.0', port=8000)