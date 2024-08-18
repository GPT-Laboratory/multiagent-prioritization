# helpers.py

import re
import random
import logging
import os
import csv
import requests
from starlette.datastructures import UploadFile
import httpx
from httpx import Timeout, AsyncClient
import asyncio
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from agent import OPENAI_URL
LLAMA_URL="https://api.groq.com/openai/v1/chat/completions"

# from app import send_to_llm

# Load environment variables from .env file
api_keys = [os.getenv(f"API-KEY{i}") for i in range(1, 4)]
llama_keys = [os.getenv(f"LLAMA-key{i}") for i in range(1, 3)]

print("API Keys: in ", api_keys)
print("GROQ keys : in ", llama_keys)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_random_temperature(low=0.1, high=0.7):
    return random.uniform(low, high)

async def estimate_ahp(data, websocket, model, topic_response, context_response):
    headers = {
        "Authorization": f"Bearer {random.choice(api_keys)}",
        "Content-Type": "application/json"
    }
    
    prioritize_prompt = construct_ahp_prompt(data, topic_response, context_response)
    estimated_factors = await send_to_llm(prioritize_prompt, headers, model)
    await stream_response_word_by_word(websocket, estimated_factors, "Final Prioritization")
    
    prioritized_stories = parse_prioritized_stories(estimated_factors)
    logger.info(f"Prioritized Stories:\n{prioritized_stories}")
    
    enriched_stories = enrich_original_stories_with_ahp(data['stories'], prioritized_stories)
    logger.info(f"Enriched Stories:\n{enriched_stories}")

    return enriched_stories

# def construct_ahp_prompt(data):
#     stories_formatted = '\n'.join([
#         f"- Story ID {story['key']}: '{story['user_story']}' {story['epic']})"
#         for story in data['stories']
#     ])
    
#     prompt = (
#         "You are a helpful assistant. Using the Analytic Hierarchy Process (AHP), prioritize the following user stories based on their relative importance.\n\n"
#         f"Here are the stories:\n{stories_formatted}\n\n"
#         "Please provide the following factors for each story on a scale of 1 to 10, where 1 represents the lowest and 10 represents the highest:\n"
#         "- Business Value (BV): The importance of this story to the business or stakeholders.\n"
#         "- Effort Required (ER): The amount of effort needed to complete this story.\n"
#         "- Dependencies (D): The extent to which this story depends on other factors or stories.\n\n"
#         "Then calculate the overall weight (W) and overall score (OS) using the following formula:\n"
#         "- W = (BV + ER + D) / 3\n"
#         "- OS = W\n\n"
#         "Return the list of stories in the following format:\n\n"
#         "### Story ID X: <Story Title>\n"
#         "- BV: <value>\n"
#         "- ER: <value>\n"
#         "- D: <value>\n"
#         "- W: <value>\n"
#         "- OS: <value>\n\n"
#         "Please maintain this exact format so that the values can be easily parsed."
#     )
    
#     return prompt

def construct_ahp_prompt(data, topic_response, context_response):
    stories_formatted = '\n'.join([
        f"- Story ID {story['key']}: '{story['user_story']}' (Epic: '{story['epic']}') - {story['description']}"
        for story in data['stories']
    ])

    topic_response_direct = '\n'.join(topic_response)
    context_response_direct = '\n'.join(context_response)

    print(stories_formatted)
    print(topic_response_direct)
    print(context_response_direct)
    
    prompt = (
        "You are a helpful assistant. Using the Analytic Hierarchy Process (AHP), prioritize the following user stories based on their relative importance.\n\n"
        f"Here are the stories:\n{stories_formatted}\n\n"
        "Previously, the following points were discussed regarding prioritization:\n"
        f"{topic_response_direct}\n\n"
        "Additionally, here is the context from prior discussions:\n"
        f"{context_response_direct}\n\n"
        "Please provide the following factors for each story on a scale of 1 to 10, where 1 represents the lowest and 10 represents the highest:\n"
        "- Business Value (BV): The importance of this story to the business or stakeholders.\n"
        "- Effort Required (ER): The amount of effort needed to complete this story.\n"
        "- Dependencies (D): The extent to which this story depends on other factors or stories.\n\n"
        "Then calculate the overall weight (W) and overall score (OS) using the following formula:\n"
        "- W = (BV + ER + D) / 3\n"
        "- OS = W\n\n"
        "Return the list of stories in the following format:\n\n"
        "### Story ID X: <Story Title>\n"
        "- BV: <value>\n"
        "- ER: <value>\n"
        "- D: <value>\n"
        "- W: <value>\n"
        "- OS: <value>\n\n"
        "For each story, provide a detailed explanation of why it received the allocated values. What is the main reason behind its prioritization? Make sure to include a complete explanation for every story."
    )
    
    return prompt


async def send_to_llm(prompt, headers, model, timeout=10000):
    print(model)
    if model == "llama3-70b-8192" or model == "mixtral-8x7b-32768":
        await asyncio.sleep(1)  # Wait for 1 second
        url = LLAMA_URL
        headers["Authorization"] = f"Bearer {random.choice(llama_keys)}"
        print(LLAMA_URL)
    else:
        url = OPENAI_URL
        headers["Authorization"] = f"Bearer {random.choice(api_keys)}"
    
    post_data = {
        "model": model,
        "messages": [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    
    response = requests.post(url, json=post_data, headers=headers, timeout=timeout)
    print(response)
    
    if response.status_code == 200:
        completion = response.json()
        completion_text = completion['choices'][0]['message']['content']
        return completion_text
    else:
        raise Exception("Failed to process the request with OpenAI")

def parse_prioritized_stories(completion_text):
    pattern = re.compile(
        r"### Story ID (\d+): ([^\n]+)\n"
        r"- BV: (\d+)\n"
        r"- ER: (\d+)\n"
        r"- D: (\d+)\n"
        r"- W: ([0-9.]+)\n"
        r"- OS: ([0-9.]+)"
    )

    prioritized_stories = []

    for match in pattern.finditer(completion_text):
        story_id, story_title, bv, er, d, weight, os = match.groups()
        prioritized_stories.append({
            "ID": int(story_id),
            "user_story": story_title,
            "BV": int(bv),
            "ER": int(er),
            "D": int(d),
            "W": float(weight),
            "OS": float(os),
        })

    # Sort the stories by overall score in descending order
    prioritized_stories.sort(key=lambda x: x["OS"], reverse=True)
    return prioritized_stories

def enrich_original_stories_with_ahp(original_stories, prioritized_stories):
    for story in original_stories:
        story_id = story['key']
        priority_data = next((item for item in prioritized_stories if item["ID"] == story_id), None)
        if priority_data:
            story.update(priority_data)
        else:
            story.update({
                "BV": 0,
                "ER": 0,
                "D": 0,
                "W": 0,
                "OS": 0,
                "priority": float('inf')
            })
            logger.warning(f"Story ID {story_id} not found in prioritized stories")

    enriched_stories = sorted(original_stories, key=lambda x: x.get('OS', 0), reverse=True)
    return enriched_stories

# End of AHP      


def construct_greetings_prompt(prioritization_technique):
    prompt = (
        "PO: Hi QA and QA, hope you're both having a great day!\n"
        "QA: Hi PO, hi Developer! I’m doing well, thanks.\n"
        "Developer: Hey PO, hey QA! All good here.\n\n"
        "PO: We have some user stories pending in our backlog that we need to prioritize.\n"
        f"QA: Let's use the {prioritization_technique} technique for prioritization.\n"
        "Developer: Sounds good to me. Let's dive in."
    )
    return prompt

# def construct_topic_prompt(stories, technique):
#     stories_formatted = construct_stories_formatted(stories)
#     prompt = (
#         "PO, QA, and Developer, please discuss the following user stories and introduce the topic of the requirements:\n\n"
#         f"{stories_formatted}\n\n"
#         f"Please consider the prioritization technique: {technique}.\n\n"
#     )
#     return prompt

# def construct_context_prompt(stories, technique):
#     stories_formatted = construct_stories_formatted(stories)
#     prompt = (
#         "PO, QA, and Developer, please discuss the context and relevance of the following user stories:\n\n"
#         f"{stories_formatted}\n\n"
#         f"Please consider the prioritization technique: {technique}.\n\n "
#     )
#     return prompt

# def construct_batch_100_dollar_prompt(data):
#     stories_formatted = '\n'.join([
#         f"- Story ID {story['key']}: '{story['user_story']}' {story['epic']} {story['description']}"
#         for story in data['stories']
#     ])

#     print(stories_formatted)
    
#     prompt = (
#         "You are a helpful assistant trained in prioritization techniques. "
#         "We need to prioritize the following user stories by distributing 100 dollars (points) among them. "
#         "The more important a story, the more dollars it should receive. "
#         "Here are the stories:\n\n"
#         f"{stories_formatted}\n\n"
#         "Please distribute 100 dollars across these stories. Each dollar represents the relative importance of that story. "
#         "Make sure the total adds up to 100 dollars. Format the output as:\n"
#         "- Story ID X: Y dollars\n"
#         "- Story ID Z: W dollars"
#     )
    
#     return prompt

def construct_topic_prompt(stories, technique):
    stories_formatted = construct_stories_formatted(stories)
    prompt = (
        "PO, QA, and Developer, please introduce the topic of the requirements for the following user stories:\n\n"
        f"{stories_formatted}\n\n"
        f"Use the prioritization technique: {technique} to prioritize, and discuss relevant points only.\n\n"
    )
    return prompt

def construct_context_prompt(stories, technique):
    stories_formatted = construct_stories_formatted(stories)
    prompt = (
        "PO, QA, and Developer, please discuss the context and relevance of the following user stories:\n\n"
        f"{stories_formatted}\n\n"
        f"Use the prioritization technique: {technique} to prioritize, and discuss relevant points only.\n\n"
    )
    return prompt

def construct_batch_100_dollar_prompt(data, topic_response, context_response):
    stories_formatted = '\n'.join([
        f"- Story ID {story['key']}: '{story['user_story']}' {story['epic']} {story['description']}"
        for story in data['stories']
    ])

    topic_response_direct = '\n'.join(topic_response)
    context_response_direct = '\n'.join(context_response)

    print(stories_formatted)
    print(topic_response_direct)
    print(context_response_direct)
    
    prompt = (
    "You are a helpful assistant trained in prioritization techniques. "
    "We need to prioritize the following user stories by distributing 100 dollars (points) among them. "
    "The more important a story, the more dollars it should receive. "
    "Here are the stories:\n\n"
    f"{stories_formatted}\n\n"
    "Previously, the following points were discussed regarding prioritization:\n"
    f"{topic_response_direct}\n\n"
    "Additionally, here is the context from prior discussions:\n"
    f"{context_response_direct}\n\n"
    "Please distribute 100 dollars across these stories. Each dollar represents the relative importance of that story. "
    "Make sure the total adds up to 100 dollars. The format for your response should strictly adhere to this pattern:\n"
    "- Story ID X: Y dollars\n"
    "- Story ID Z: W dollars\n\n"
    "Please provide a detailed explanation for the allocation of dollars to each story, specifying the main reason behind its prioritization. "
    "Include a complete explanation for every story, ensuring that each justification aligns with the discussed topics and context."
    )
    return prompt
    
   

# def construct_batch_100_dollar_prompt(data, topic_response, context_response):
#     stories_formatted = '\n'.join([
#         f"- Story ID {story['key']}: '{story['user_story']}' {story['epic']} {story['description']}"
#         for story in data['stories']
#     ])

#     topic_response_formatted = '\n'.join([f"- {response}" for response in topic_response])
#     context_response_formatted = '\n'.join([f"- {response}" for response in context_response])

#     print(stories_formatted)
#     print(topic_response_formatted)
#     print(context_response_formatted)
    
#     prompt = (
#         "You are a helpful assistant trained in prioritization techniques. "
#         "We need to prioritize the following user stories by distributing 100 dollars (points) among them. "
#         "The more important a story, the more dollars it should receive. "
#         "Here are the stories:\n\n"
#         f"{stories_formatted}\n\n"
#         "Previously, the following points were discussed regarding prioritization:\n"
#         f"{topic_response_formatted}\n\n"
#         "Additionally, here is the context from prior discussions:\n"
#         f"{context_response_formatted}\n\n"
#         "Please distribute 100 dollars across these stories. Each dollar represents the relative importance of that story. "
#         "Make sure the total adds up to 100 dollars. Format the output as:\n"
#         "- Story ID X: Y dollars\n"
#         "- Story ID Z: W dollars\n\n"
#         "Critique: Explain why the agents prioritize the user stories in this way. What are the reasons behind their prioritization decisions?"
#     )
    
#     return prompt

def parse_100_dollar_response(response_text):
    pattern = re.compile(r"- Story ID (\d+): .*?(\d+) dollars")
    dollar_distribution = []

    for match in pattern.finditer(response_text):
        story_id, dollars = match.groups()
        dollar_distribution.append({
            'story_id': int(story_id),
            'dollars': int(dollars)
        })

    return dollar_distribution

def validate_dollar_distribution(dollar_distribution, stories):
    total_dollars = sum(dist['dollars'] for dist in dollar_distribution)
    story_ids = {story['key'] for story in stories}
    response_story_ids = {dist['story_id'] for dist in dollar_distribution}
    
    return total_dollars == 100 and story_ids == response_story_ids

def enrich_stories_with_dollar_distribution(original_stories, dollar_distribution):
    dollar_dict = {dist['story_id']: dist['dollars'] for dist in dollar_distribution}

    enriched_stories = []
    for story in original_stories:
        story_id = story['key']
        story['dollar_allocation'] = dollar_dict.get(story_id, 0)
        enriched_stories.append(story)

    # Sort the enriched stories by dollar_allocation in descending order
    enriched_stories.sort(key=lambda x: x['dollar_allocation'], reverse=True)
    
    return enriched_stories

def construct_stories_formatted(stories):
    return '\n'.join([
        f"- Story ID {story['key']}: '{story['user_story']}' {story['epic']} {story['description']}  "
        for story in stories
    ])

def ensure_unique_keys(stories):
    seen = {}
    for story in stories:
        key = story['key']
        if key in seen:
            seen[key] += 1
            story['key'] = f"{key}_{seen[key]}"
        else:
            seen[key] = 0
    return stories


async def estimate_wsjf(data, websocket, model, topic_response, context_response):
    headers = {
        "Authorization": f"Bearer {random.choice(api_keys)}",
        "Content-Type": "application/json"
    }
    prioritize_prompt = construct_batch_wsjf_prompt(data)
    # prioritize_prompt = construct_batch_wsjf_prompt(data, topic_response, context_response)
    #logger.info(f"Prioritize Prompt:\n{prioritize_prompt}")  # Debugging print
    estimated_factors = await send_to_llm(prioritize_prompt, headers, model)
    await stream_response_word_by_word(websocket, estimated_factors, "Final Prioritization")
    #logger.info(f"Estimated Factor:\n{estimated_factors}")
    
    wsjf_factors = parse_wsjf_response(estimated_factors)
    logger.info(f"wsjf_factors Factor:\n{wsjf_factors}")
    

    enriched_stories = enrich_original_stories_with_wsjf(data, wsjf_factors)
    logger.info(f"wsjf_factors Factor:\n{enriched_stories}")

    return enriched_stories
    
def construct_batch_wsjf_prompt(stories):
    stories_formatted = '\n'.join([
        f"- Story ID {story['key']}: '{story['user_story']}' {story['epic']} "
        for story in stories
    ])
    
    prompt = (
        "You are a helpful assistant trained in WSJF factor estimation. "
        "For each of the following user stories, please provide estimated numeric values (scale 1 to 10) for the WSJF factors:\n\n"
        f"Here are the stories:\n{stories_formatted}\n\n"
        "Please consider the following factors and provide values on a scale of 1 to 10, where 1 represents the lowest impact or effort and 10 represents the highest:\n"
        "- Business Value (BV): The relative importance of this story to the business or stakeholders.\n"
        "- Time Criticality (TC): The urgency of delivering this story sooner rather than later.\n"
        "- Risk Reduction/Opportunity Enablement (RR/OE): The extent to which delivering this story can reduce risks or enable new opportunities.\n"
        "- Job Size (JS): The amount of effort required to complete this story, typically measured in story points or ideal days.\n\n"
        "Format the output as:\n"
        "- Story ID X: (Epic: Y)\n"
        "  - Business Value (BV): Z\n"
        "  - Time Criticality (TC): W\n"
        "  - Risk Reduction/Opportunity Enablement (RR/OE): V\n"
        "  - Job Size (JS): U\n"
    )
    
    return prompt

# def construct_batch_wsjf_prompt(stories, topic_response, context_response):
#     stories_formatted = '\n'.join([
#         f"- Story ID {story['key']}: '{story['user_story']}' (Epic: '{story['epic']}') - {story['description']}"
#         for story in stories
#     ])
    
#     topic_response_direct = '\n'.join(topic_response)
#     context_response_direct = '\n'.join(context_response)
    
#     #print(stories_formatted)
#     #print(topic_response_direct)
#     #print(context_response_direct)
    
#     prompt = (
#         "You are a helpful assistant trained in WSJF factor estimation. "
#         "For each of the following user stories, please provide estimated numeric values (scale 1 to 10) for the WSJF factors:\n\n"
#         f"Here are the stories:\n{stories_formatted}\n\n"
#         "Previously, the following points were discussed regarding prioritization:\n"
#         f"{topic_response_direct}\n\n"
#         "Additionally, here is the context from prior discussions:\n"
#         f"{context_response_direct}\n\n"
#         "Please consider the following factors and provide values on a scale of 1 to 10, where 1 represents the lowest impact or effort and 10 represents the highest:\n"
#         "- Business Value (BV): The relative importance of this story to the business or stakeholders.\n"
#         "- Time Criticality (TC): The urgency of delivering this story sooner rather than later.\n"
#         "- Risk Reduction/Opportunity Enablement (RR/OE): The extent to which delivering this story can reduce risks or enable new opportunities.\n"
#         "- Job Size (JS): The amount of effort required to complete this story, typically measured in story points or ideal days.\n\n"
#         "Format the output as:\n"
#         "- Story ID X: (Epic: Y)\n"
#         "  - Business Value (BV): Z\n"
#         "  - Time Criticality (TC): W\n"
#         "  - Risk Reduction/Opportunity Enablement (RR/OE): V\n"
#         "  - Job Size (JS): U\n\n"
#         "For each story, provide a detailed explanation of why it received the allocated values. What is the main reason behind its prioritization? Make sure to include a complete explanation for every story."
#     )
    
#     return prompt
    

def parse_wsjf_response(response_text):
    pattern = re.compile(
        r"- Story ID (\d+): \(Epic: .+?\)\n"  # Capture the story ID and the epic
        r"\s+- Business Value \(BV\): (\d+)\n"  # Capture Business Value
        r"\s+- Time Criticality \(TC\): (\d+)\n"  # Capture Time Criticality
        r"\s+- Risk Reduction/Opportunity Enablement \(RR/OE\): (\d+)\n"  # Capture Risk Reduction/Opportunity Enablement
        r"\s+- Job Size \(JS\): (\d+)"  # Capture Job Size
    )

    wsjf_factors = []
    for match in pattern.finditer(response_text):
        story_id, bv, tc, rr_oe, js = match.groups()
        wsjf_factors.append({
            'story_id': int(story_id),
            'wsjf_factors': {
                'BV': int(bv),
                'TC': int(tc),
                'RR/OE': int(rr_oe),
                'JS': int(js)
            }
        })

    return wsjf_factors

def validate_wsjf_response(wsjf_factors, stories):
    story_ids = {story['key'] for story in stories}
    response_story_ids = {factor['story_id'] for factor in wsjf_factors}
    
    if story_ids == response_story_ids:
        return True
    return False

def enrich_original_stories_with_wsjf(original_stories, wsjf_factors):
    wsjf_dict = {factor['story_id']: factor['wsjf_factors'] for factor in wsjf_factors}
    logger.info(f"Original stories:\n{original_stories}")
    logger.info(f"WSJF factors dictionary:\n{wsjf_dict}")

    enriched_stories = []
    for story in original_stories:
        story_id = story['key']
        if story_id in wsjf_dict:
            wsjf_data = wsjf_dict[story_id]
            story['wsjf_factors'] = wsjf_data
            bv = wsjf_data['BV']
            tc = wsjf_data['TC']
            rr_oe = wsjf_data['RR/OE']
            js = wsjf_data['JS']
            wsjf_score = (bv + tc + rr_oe) / js if js != 0 else 0  # Prevent division by zero
            story['wsjf_score'] = wsjf_score
            story['bv'] = bv
            story['tc'] = tc
            story['oe'] = rr_oe
            story['js'] =  js 

            logger.info(f"Story ID {story_id} WSJF factors: {wsjf_data}, WSJF score: {wsjf_score}")
        else:
            story['wsjf_factors'] = {
                'BV': 0,
                'TC': 0,
                'RR/OE': 0,
                'JS': 0
            }
            story['wsjf_score'] = 0
            story['bv'] = 0
            story['tc'] = 0
            logger.warning(f"Story ID {story_id} not found in WSJF factors")

        enriched_stories.append(story)

    enriched_stories = sort_stories_by_wsjf_in_place(enriched_stories)
    return enriched_stories

def sort_stories_by_wsjf_in_place(enriched_stories):
    return sorted(enriched_stories, key=lambda story: story.get('wsjf_score', 0), reverse=True)

    enriched_sorted_stories = sort_stories_by_wsjf_in_place(enriched_stories)
    logger.info(f"Append and Sorted:\n{enriched_sorted_stories}")
    return enriched_sorted_stories


def save_uploaded_file(upload_folder, file: UploadFile):
    if not file.filename.endswith('.csv'):
        return None, 'Unsupported file type'

    file_path = os.path.join(upload_folder, file.filename)
    with open(file_path, 'wb') as f:
        f.write(file.file.read())
    return file_path, None

def parse_csv_to_json(file_path):
    with open(file_path, mode='r', encoding='utf-8') as csv_file:
        csv_reader = csv.DictReader(csv_file)
        csv_data = list(csv_reader)  # Convert CSV file content to a list of dictionaries
    os.remove(file_path)  # Optional: remove the file after processing
    return csv_data

#     raise Exception("Failed to get response from OpenAI after multiple attempts")

# Implement MOSCOW Technique 


# MoSCoW Functions

async def estimate_moscow(data, websocket, model, topic_response, context_response):
    headers = {
        "Authorization": f"Bearer {random.choice(api_keys)}",
        "Content-Type": "application/json"
    }
    
    prioritize_prompt = construct_batch_moscow_prompt(data, topic_response, context_response)
    estimated_priorities = await send_to_llm(prioritize_prompt, headers, model)
    await stream_response_word_by_word(websocket, estimated_priorities, "Final Prioritization")
    
    moscow_priorities = parse_moscow_response(estimated_priorities)
    logger.info(f"MoSCoW Priorities:\n{moscow_priorities}")
    
    enriched_stories = enrich_original_stories_with_moscow(data, moscow_priorities)
    logger.info(f"Enriched Stories with MoSCoW Priorities:\n{enriched_stories}")

    return enriched_stories

# def construct_batch_moscow_prompt(stories):
#     stories_formatted = '\n'.join([
#         f"- Story ID {story['key']}: '{story['user_story']}' {story['epic']} {story['description']}"
#         for story in stories
#     ])
    
#     prompt = (
#         "You are a helpful assistant trained in MoSCoW prioritization. "
#         "For each of the following user stories, please classify them into one of the following categories based on their importance:\n"
#         "- Must Have\n"
#         "- Should Have\n"
#         "- Could Have\n"
#         "- Won't Have\n\n"
#         "Here are the stories:\n\n"
#         f"{stories_formatted}\n\n"
#         "Format the output as:\n"
#         "- Story ID X: Category"
#     )
    
#     return prompt

def construct_batch_moscow_prompt(stories, topic_response, context_response):
    stories_formatted = '\n'.join([
        f"- Story ID {story['key']}: '{story['user_story']}' (Epic: '{story['epic']}') - {story['description']}"
        for story in stories
    ])

    topic_response_direct = '\n'.join(topic_response)
    context_response_direct = '\n'.join(context_response)

    print(stories_formatted)
    print(topic_response_direct)
    print(context_response_direct)
    
    prompt = (
        "You are a helpful assistant trained in MoSCoW prioritization. "
        "For each of the following user stories, please classify them into one of the following categories based on their importance:\n"
        "- Must Have\n"
        "- Should Have\n"
        "- Could Have\n"
        "- Won't Have\n\n"
        "Here are the stories:\n\n"
        f"{stories_formatted}\n\n"
        "Previously, the following points were discussed regarding prioritization:\n"
        f"{topic_response_direct}\n\n"
        "Additionally, here is the context from prior discussions:\n"
        f"{context_response_direct}\n\n"
        "Format the output as:\n"
        "- Story ID X: Category\n\n"
        "For each story, provide a detailed explanation of why it received the allocated category. What is the main reason behind its prioritization? Make sure to include a complete explanation for every story."
    )
    
    return prompt

def parse_moscow_response(response_text):
    pattern = re.compile(r"- Story ID (\d+): (Must Have|Should Have|Could Have|Won't Have)")
    moscow_priorities = []

    for match in pattern.finditer(response_text):
        story_id, category = match.groups()
        moscow_priorities.append({
            'story_id': int(story_id),
            'category': category
        })

    return moscow_priorities

def validate_moscow_response(moscow_priorities, stories):
    story_ids = {story['key'] for story in stories}
    response_story_ids = {priority['story_id'] for priority in moscow_priorities}
    
    return story_ids == response_story_ids

def enrich_original_stories_with_moscow(original_stories, moscow_priorities):
    moscow_dict = {priority['story_id']: priority['category'] for priority in moscow_priorities}

    enriched_stories = []
    for story in original_stories:
        story_id = story['key']
        story['moscow_category'] = moscow_dict.get(story_id, "No Category")
        enriched_stories.append(story)

    enriched_stories.sort(key=lambda x: ['Must Have', 'Should Have', 'Could Have', "Won't Have"].index(x['moscow_category']))
    
    return enriched_stories



# Close Moscow Technique


# KANO Functions
async def estimate_kano(data, websocket, model, topic_response, context_response):
    headers = {
        "Authorization": f"Bearer {random.choice(api_keys)}",
        "Content-Type": "application/json"
    }
    
    prioritize_prompt = construct_batch_kano_prompt(data, topic_response, context_response)
    estimated_priorities = await send_to_llm(prioritize_prompt, headers, model, )
    await stream_response_word_by_word(websocket, estimated_priorities, "Final Prioritization")
    
    kano_priorities = parse_kano_response(estimated_priorities)
    logger.info(f"KANO Priorities:\n{kano_priorities}")
    
    enriched_stories = enrich_original_stories_with_kano(data, kano_priorities)
    logger.info(f"Enriched Stories with KANO Priorities:\n{enriched_stories}")

    return enriched_stories

# def construct_batch_kano_prompt(stories):
#     stories_formatted = '\n'.join([
#         f"- Story ID {story['key']}: '{story['user_story']}' {story['epic'] {story['description']} "
#         for story in stories
#     ])
    
#     prompt = (
#         "You are a helpful assistant trained in KANO model prioritization. "
#         "For each of the following user stories, please classify them into one of the following categories based on their importance:\n"
#         "- Basic Needs\n"
#         "- Performance Needs\n"
#         "- Excitement Needs\n"
#         "- Indifferent\n"
#         "- Reverse\n\n"
#         "Here are the stories:\n\n"
#         f"{stories_formatted}\n\n"
#         "Format the output as:\n"
#         "- Story ID X: Category"
#     )
    
#     return prompt

def construct_batch_kano_prompt(stories, topic_response, context_response):
    stories_formatted = '\n'.join([
        f"- Story ID {story['key']}: '{story['user_story']}' (Epic: '{story['epic']}') - {story['description']}"
        for story in stories
    ])
    
    topic_response_direct = '\n'.join(topic_response)
    context_response_direct = '\n'.join(context_response)
    
    print(stories_formatted)
    print(topic_response_direct)
    print(context_response_direct)
    
    prompt = (
        "You are a helpful assistant trained in KANO model prioritization. "
        "For each of the following user stories, please classify them into one of the following categories based on their importance:\n"
        "- Basic Needs\n"
        "- Performance Needs\n"
        "- Excitement Needs\n"
        "- Indifferent\n"
        "- Reverse\n\n"
        "Here are the stories:\n\n"
        f"{stories_formatted}\n\n"
        "Previously, the following points were discussed regarding prioritization:\n"
        f"{topic_response_direct}\n\n"
        "Additionally, here is the context from prior discussions:\n"
        f"{context_response_direct}\n\n"
        "Format the output as:\n"
        "- Story ID X: Category\n\n"
        "For each story, provide a detailed explanation of why it received the allocated category. What is the main reason behind its prioritization? Make sure to include a complete explanation for every story."
    )
    
    return prompt

def parse_kano_response(response_text):
    pattern = re.compile(r"- Story ID (\d+): (Basic Needs|Performance Needs|Excitement Needs|Indifferent|Reverse)")
    kano_priorities = []

    for match in pattern.finditer(response_text):
        story_id, category = match.groups()
        kano_priorities.append({
            'story_id': int(story_id),
            'category': category
        })

    return kano_priorities

def validate_kano_response(kano_priorities, stories):
    story_ids = {story['key'] for story in stories}
    response_story_ids = {priority['story_id'] for priority in kano_priorities}
    
    return story_ids == response_story_ids

def enrich_original_stories_with_kano(original_stories, kano_priorities):
    kano_dict = {priority['story_id']: priority['category'] for priority in kano_priorities}

    enriched_stories = []
    for story in original_stories:
        story_id = story['key']
        story['kano_category'] = kano_dict.get(story_id, "No Category")
        enriched_stories.append(story)

    enriched_stories.sort(key=lambda x: ['Basic Needs', 'Performance Needs', 'Excitement Needs', 'Indifferent', 'Reverse'].index(x['kano_category']))
    
    return enriched_stories

#close KANO TECHNIQUE


async def stream_response_word_by_word(websocket, response, agent_type, delay=0.6):

    if websocket.application_state != WebSocketState.DISCONNECTED:
        await websocket.send_json({
            "agentType": agent_type,
            "message": response
        })
        await asyncio.sleep(delay)  # Delay to simulate streaming effect  


