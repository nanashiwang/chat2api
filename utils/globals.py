import json
import os

import utils.configs as configs
from utils.Logger import logger

DATA_FOLDER = "data"
TOKENS_FILE = os.path.join(DATA_FOLDER, "token.txt")
REFRESH_MAP_FILE = os.path.join(DATA_FOLDER, "refresh_map.json")
ERROR_TOKENS_FILE = os.path.join(DATA_FOLDER, "error_token.txt")
WSS_MAP_FILE = os.path.join(DATA_FOLDER, "wss_map.json")
FP_FILE = os.path.join(DATA_FOLDER, "fp_map.json")
ROUTING_CONFIG_FILE = os.path.join(DATA_FOLDER, "routing_config.json")
SEED_MAP_FILE = os.path.join(DATA_FOLDER, "seed_map.json")
CONVERSATION_MAP_FILE = os.path.join(DATA_FOLDER, "conversation_map.json")

count = 0
token_list = []
error_token_list = []
refresh_map = {}
wss_map = {}
fp_map = {}
routing_config = {}
seed_map = {}
conversation_map = {}
impersonate_list = [
    "chrome119",
    "chrome120",
    "chrome123",
] if not configs.impersonate_list else configs.impersonate_list

if not os.path.exists(DATA_FOLDER):
    os.makedirs(DATA_FOLDER)

if os.path.exists(REFRESH_MAP_FILE):
    with open(REFRESH_MAP_FILE, "r") as f:
        try:
            refresh_map = json.load(f)
        except:
            refresh_map = {}
else:
    refresh_map = {}

if os.path.exists(WSS_MAP_FILE):
    with open(WSS_MAP_FILE, "r") as f:
        try:
            wss_map = json.load(f)
        except:
            wss_map = {}
else:
    wss_map = {}

if os.path.exists(FP_FILE):
    with open(FP_FILE, "r", encoding="utf-8") as f:
        try:
            fp_map = json.load(f)
        except:
            fp_map = {}
else:
    fp_map = {}

if os.path.exists(ROUTING_CONFIG_FILE):
    with open(ROUTING_CONFIG_FILE, "r", encoding="utf-8") as f:
        try:
            routing_config = json.load(f)
        except:
            routing_config = {}
else:
    routing_config = {}

if os.path.exists(SEED_MAP_FILE):
    with open(SEED_MAP_FILE, "r") as f:
        try:
            seed_map = json.load(f)
        except:
            seed_map = {}
else:
    seed_map = {}

if os.path.exists(CONVERSATION_MAP_FILE):
    with open(CONVERSATION_MAP_FILE, "r") as f:
        try:
            conversation_map = json.load(f)
        except:
            conversation_map = {}
else:
    conversation_map = {}

if os.path.exists(TOKENS_FILE):
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                token_list.append(line.strip())
else:
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        pass

if os.path.exists(ERROR_TOKENS_FILE):
    with open(ERROR_TOKENS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                error_token_list.append(line.strip())
else:
    with open(ERROR_TOKENS_FILE, "w", encoding="utf-8") as f:
        pass

if token_list:
    logger.info(f"Token list count: {len(token_list)}, Error token list count: {len(error_token_list)}")
    logger.info("-" * 60)
