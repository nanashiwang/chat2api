import ast
import os

from dotenv import load_dotenv

from utils.Logger import logger

load_dotenv(encoding="ascii")


def is_true(x):
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.lower() in ['true', '1', 't', 'y', 'yes']
    elif isinstance(x, int):
        return x == 1
    else:
        return False


api_prefix = os.getenv('API_PREFIX', None)
authorization = os.getenv('AUTHORIZATION', '').replace(' ', '')
admin_password = os.getenv('ADMIN_PASSWORD', None)
chatgpt_base_url = os.getenv('CHATGPT_BASE_URL', 'https://chatgpt.com').replace(' ', '')
auth_key = os.getenv('AUTH_KEY', None)
x_sign = os.getenv('X_SIGN', None)

ark0se_token_url = os.getenv('ARK' + 'OSE_TOKEN_URL', '').replace(' ', '')
if not ark0se_token_url:
    ark0se_token_url = os.getenv('ARK0SE_TOKEN_URL', None)
proxy_url = os.getenv('PROXY_URL', '').replace(' ', '')
sentinel_proxy_url = os.getenv('SENTINEL_PROXY_URL', None)
export_proxy_url = os.getenv('EXPORT_PROXY_URL', None)
file_host = os.getenv('FILE_HOST', None)
voice_host = os.getenv('VOICE_HOST', None)
impersonate_list_str = os.getenv('IMPERSONATE', '[]')
user_agents_list_str = os.getenv('USER_AGENTS', '[]')
device_tuple_str = os.getenv('DEVICE_TUPLE', '()')
browser_tuple_str = os.getenv('BROWSER_TUPLE', '()')
platform_tuple_str = os.getenv('PLATFORM_TUPLE', '()')

cf_file_url = os.getenv('CF_FILE_URL', None)
turnstile_solver_url = os.getenv('TURNSTILE_SOLVER_URL', None)

history_disabled = is_true(os.getenv('HISTORY_DISABLED', True))
pow_difficulty = os.getenv('POW_DIFFICULTY', '000032')
retry_times = int(os.getenv('RETRY_TIMES', 3))
conversation_only = is_true(os.getenv('CONVERSATION_ONLY', False))
enable_limit = is_true(os.getenv('ENABLE_LIMIT', True))
upload_by_url = is_true(os.getenv('UPLOAD_BY_URL', False))
check_model = is_true(os.getenv('CHECK_MODEL', False))
scheduled_refresh = is_true(os.getenv('SCHEDULED_REFRESH', False))
random_token = is_true(os.getenv('RANDOM_TOKEN', True))
oai_language = os.getenv('OAI_LANGUAGE', 'en-US')
chat_requirements_timeout = int(os.getenv('CHAT_REQUIREMENTS_TIMEOUT', 15))
chat_request_timeout = int(os.getenv('CHAT_REQUEST_TIMEOUT', 30))
accept_language = os.getenv('ACCEPT_LANGUAGE', 'en-US,en;q=0.9')
client_timezone = os.getenv('CLIENT_TIMEZONE', 'America/Los_Angeles')
client_timezone_offset_min = int(os.getenv('CLIENT_TIMEZONE_OFFSET_MIN', -480))

authorization_list = authorization.split(',') if authorization else []
chatgpt_base_url_list = chatgpt_base_url.split(',') if chatgpt_base_url else []
ark0se_token_url_list = ark0se_token_url.split(',') if ark0se_token_url else []
proxy_url_list = proxy_url.split(',') if proxy_url else []
sentinel_proxy_url_list = sentinel_proxy_url.split(',') if sentinel_proxy_url else []
impersonate_list = ast.literal_eval(impersonate_list_str)
user_agents_list = ast.literal_eval(user_agents_list_str)
device_tuple = ast.literal_eval(device_tuple_str)
browser_tuple = ast.literal_eval(browser_tuple_str)
platform_tuple = ast.literal_eval(platform_tuple_str)

enable_gateway = is_true(os.getenv('ENABLE_GATEWAY', False))
auto_seed = is_true(os.getenv('AUTO_SEED', True))
force_no_history = is_true(os.getenv('FORCE_NO_HISTORY', False))
no_sentinel = is_true(os.getenv('NO_SENTINEL', False))
init_tokens = os.getenv('INIT_TOKENS', '')
init_proxies = os.getenv('INIT_PROXIES', '')
init_group_size = int(os.getenv('INIT_GROUP_SIZE', 25))
init_apply_on_empty = is_true(os.getenv('INIT_APPLY_ON_EMPTY', True))
init_force = is_true(os.getenv('INIT_FORCE', False))

# ========================= Antiban (风控规避层) =========================
# 总开关；默认关闭，保持向后兼容
enable_antiban = is_true(os.getenv('ENABLE_ANTIBAN', False))
# IP 粘性桶：每个代理最多容纳的账号数
bucket_max_accounts_per_ip = int(os.getenv('BUCKET_MAX_ACCOUNTS_PER_IP', 5))
# 严格 IP 绑定：开启后账号一旦绑定 IP 即永不漂移
strict_ip_binding = is_true(os.getenv('STRICT_IP_BINDING', True))
# 账号级最小请求间隔秒数（Team/Plus 默认 60s）
account_min_interval_seconds = int(os.getenv('ACCOUNT_MIN_INTERVAL_SECONDS', 60))
# 免费账号最小请求间隔秒数（通常需更长）
free_account_min_interval_seconds = int(os.getenv('FREE_ACCOUNT_MIN_INTERVAL_SECONDS', 180))
# 冷却抖动比例（±jitter）
account_cooldown_jitter = float(os.getenv('ACCOUNT_COOLDOWN_JITTER', 0.3))
# 账号排队最长等待秒数；超过则返回 503 让上游切换
account_max_wait_seconds = int(os.getenv('ACCOUNT_MAX_WAIT_SECONDS', 30))
# Geo 查询服务提供商：ip-api | ipinfo
ip_geo_provider = os.getenv('IP_GEO_PROVIDER', 'ip-api')
# Geo 缓存 TTL（天）
ip_geo_cache_ttl_days = int(os.getenv('IP_GEO_CACHE_TTL_DAYS', 30))
# 熔断参数
circuit_429_cooldown = int(os.getenv('CIRCUIT_429_COOLDOWN', 1800))
circuit_403_cooldown = int(os.getenv('CIRCUIT_403_COOLDOWN', 3600))
circuit_dead_account_recheck_hours = int(os.getenv('CIRCUIT_DEAD_ACCOUNT_RECHECK_HOURS', 24))
circuit_bucket_heal_minutes = int(os.getenv('CIRCUIT_BUCKET_HEAL_MINUTES', 30))

with open('version.txt') as f:
    version = f.read().strip()

logger.info("-" * 60)
logger.info(f"Chat2Api {version} | https://github.com/lanqian528/chat2api")
logger.info("-" * 60)
logger.info("Environment variables:")
logger.info("------------------------- Security -------------------------")
logger.info("API_PREFIX:        " + str(api_prefix))
logger.info("AUTHORIZATION:     " + str(authorization_list))
logger.info("ADMIN_PASSWORD:    " + str(bool(admin_password)))
logger.info("AUTH_KEY:          " + str(auth_key))
logger.info("------------------------- Request --------------------------")
logger.info("CHATGPT_BASE_URL:  " + str(chatgpt_base_url_list))
logger.info("PROXY_URL:         " + str(proxy_url_list))
logger.info("EXPORT_PROXY_URL:  " + str(export_proxy_url))
logger.info("FILE_HOST:     " + str(file_host))
logger.info("VOICE_HOST:    " + str(voice_host))
logger.info("IMPERSONATE:       " + str(impersonate_list))
logger.info("USER_AGENTS:       " + str(user_agents_list))
logger.info("---------------------- Functionality -----------------------")
logger.info("HISTORY_DISABLED:  " + str(history_disabled))
logger.info("POW_DIFFICULTY:    " + str(pow_difficulty))
logger.info("RETRY_TIMES:       " + str(retry_times))
logger.info("CONVERSATION_ONLY: " + str(conversation_only))
logger.info("ENABLE_LIMIT:      " + str(enable_limit))
logger.info("UPLOAD_BY_URL:     " + str(upload_by_url))
logger.info("CHECK_MODEL:       " + str(check_model))
logger.info("SCHEDULED_REFRESH: " + str(scheduled_refresh))
logger.info("RANDOM_TOKEN:      " + str(random_token))
logger.info("OAI_LANGUAGE:      " + str(oai_language))
logger.info("ACCEPT_LANGUAGE:   " + str(accept_language))
logger.info("CLIENT_TIMEZONE:   " + str(client_timezone))
logger.info("CLIENT_TZ_OFFSET:  " + str(client_timezone_offset_min))
logger.info("CHAT_REQUIREMENTS_TIMEOUT: " + str(chat_requirements_timeout))
logger.info("CHAT_REQUEST_TIMEOUT:      " + str(chat_request_timeout))
logger.info("------------------------- Gateway --------------------------")
logger.info("ENABLE_GATEWAY:    " + str(enable_gateway))
logger.info("AUTO_SEED:         " + str(auto_seed))
logger.info("FORCE_NO_HISTORY: " + str(force_no_history))
logger.info("INIT_TOKENS:       " + str(bool(init_tokens)))
logger.info("INIT_PROXIES:      " + str(bool(init_proxies)))
logger.info("INIT_GROUP_SIZE:   " + str(init_group_size))
logger.info("INIT_FORCE:        " + str(init_force))
logger.info("------------------------- Antiban --------------------------")
logger.info("ENABLE_ANTIBAN:    " + str(enable_antiban))
logger.info("STRICT_IP_BINDING: " + str(strict_ip_binding))
logger.info("BUCKET_MAX_ACCOUNTS_PER_IP: " + str(bucket_max_accounts_per_ip))
logger.info("ACCOUNT_MIN_INTERVAL_SECONDS: " + str(account_min_interval_seconds))
logger.info("ACCOUNT_MAX_WAIT_SECONDS:     " + str(account_max_wait_seconds))
logger.info("IP_GEO_PROVIDER:   " + str(ip_geo_provider))
logger.info("CIRCUIT_429_COOLDOWN: " + str(circuit_429_cooldown))
logger.info("CIRCUIT_403_COOLDOWN: " + str(circuit_403_cooldown))
logger.info("-" * 60)
