import asyncio
import types

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Request, HTTPException, Form, Security
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from starlette.background import BackgroundTask

import utils.globals as globals
from app import app, templates, security_scheme
from chatgpt.ChatService import ChatService
from chatgpt.authorization import refresh_all_tokens
from utils.bootstrap import initialize_from_env
from utils.Logger import logger
from utils.configs import api_prefix, scheduled_refresh, history_disabled
from utils.retry import async_retry
from utils import antiban
from utils.antiban import circuit as antiban_circuit

scheduler = AsyncIOScheduler()


@app.on_event("startup")
async def app_start():
    initialize_from_env()
    await antiban.init()

    # Antiban 自愈定时任务
    from utils.configs import enable_antiban, circuit_bucket_heal_minutes
    if enable_antiban:
        scheduler.add_job(
            id='antiban_heal',
            func=antiban_circuit.scheduled_heal,
            trigger='interval',
            minutes=max(int(circuit_bucket_heal_minutes), 5),
        )

    if scheduled_refresh:
        scheduler.add_job(id='refresh', func=refresh_all_tokens, trigger='cron', hour=3, minute=0, day='*/2',
                          kwargs={'force_refresh': True})
        scheduler.start()
        asyncio.get_event_loop().call_later(0, lambda: asyncio.create_task(refresh_all_tokens(force_refresh=False)))
    elif enable_antiban:
        # 只有 antiban 启用、没启用 refresh 时，也需要把 scheduler 跑起来
        scheduler.start()


async def to_send_conversation(request_data, req_token):
    chat_service = ChatService(req_token)
    try:
        await chat_service.set_dynamic_data(request_data)
        await chat_service.get_chat_requirements()
        return chat_service
    except HTTPException as e:
        await chat_service.close_client()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        await chat_service.close_client()
        logger.error(f"Server error, {str(e)}")
        raise HTTPException(status_code=500, detail="Server error")


async def process(request_data, req_token):
    chat_service = await to_send_conversation(request_data, req_token)
    try:
        await chat_service.prepare_send_conversation()
        res = await chat_service.send_conversation()
        return chat_service, res
    except HTTPException as e:
        await chat_service.close_client()
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        await chat_service.close_client()
        logger.error(f"Server error, {str(e)}")
        raise HTTPException(status_code=500, detail="Server error")


def parse_bool_query(value, default):
    if value is None:
        return default
    return str(value).lower() in ['true', '1', 't', 'y', 'yes']


def format_models_response(model_slugs):
    data = []
    for model_slug in sorted(model_slugs):
        data.append({
            "id": model_slug,
            "object": "model",
            "created": 0,
            "owned_by": "openai",
        })
    return {
        "object": "list",
        "data": data,
    }


@app.post(f"/{api_prefix}/v1/chat/completions" if api_prefix else "/v1/chat/completions")
async def send_conversation(request: Request, credentials: HTTPAuthorizationCredentials = Security(security_scheme)):
    req_token = credentials.credentials
    try:
        request_data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "Invalid JSON body"})
    chat_service, res = await async_retry(process, request_data, req_token)
    try:
        if isinstance(res, types.AsyncGeneratorType):
            background = BackgroundTask(chat_service.close_client)
            return StreamingResponse(res, media_type="text/event-stream", background=background)
        else:
            background = BackgroundTask(chat_service.close_client)
            return JSONResponse(res, media_type="application/json", background=background)
    except HTTPException as e:
        await chat_service.close_client()
        if e.status_code == 500:
            logger.error(f"Server error, {str(e)}")
            raise HTTPException(status_code=500, detail="Server error")
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        await chat_service.close_client()
        logger.error(f"Server error, {str(e)}")
        raise HTTPException(status_code=500, detail="Server error")


@app.get(f"/{api_prefix}/v1/models" if api_prefix else "/v1/models")
async def list_models(request: Request, credentials: HTTPAuthorizationCredentials = Security(security_scheme)):
    chat_service = ChatService(credentials.credentials)
    try:
        await chat_service.resolve_auth_context()
        chat_service.history_disabled = parse_bool_query(
            request.query_params.get("history_disabled", request.query_params.get("history_and_training_disabled")),
            history_disabled,
        )
        request_account_id = request.headers.get("ChatGPT-Account-ID") or request.headers.get("Chatgpt-Account-Id")
        if request_account_id:
            chat_service.account_id = request_account_id
        await chat_service.initialize_request_context()
        model_slugs = await chat_service.fetch_available_models()
        return JSONResponse(format_models_response(model_slugs), media_type="application/json")
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        logger.error(f"Server error, {str(e)}")
        raise HTTPException(status_code=500, detail="Server error")
    finally:
        await chat_service.close_client()


@app.get(f"/{api_prefix}/tokens" if api_prefix else "/tokens", response_class=HTMLResponse)
async def upload_html(request: Request):
    tokens_count = len(set(globals.token_list) - set(globals.error_token_list))
    return templates.TemplateResponse("tokens.html",
                                      {"request": request, "api_prefix": api_prefix, "tokens_count": tokens_count})


@app.post(f"/{api_prefix}/tokens/upload" if api_prefix else "/tokens/upload")
async def upload_post(text: str = Form(...)):
    lines = text.split("\n")
    for line in lines:
        if line.strip() and not line.startswith("#"):
            globals.token_list.append(line.strip())
            with open(globals.TOKENS_FILE, "a", encoding="utf-8") as f:
                f.write(line.strip() + "\n")
    logger.info(f"Token count: {len(globals.token_list)}, Error token count: {len(globals.error_token_list)}")
    tokens_count = len(set(globals.token_list) - set(globals.error_token_list))
    return {"status": "success", "tokens_count": tokens_count}


@app.post(f"/{api_prefix}/tokens/clear" if api_prefix else "/tokens/clear")
async def clear_tokens():
    globals.token_list.clear()
    globals.error_token_list.clear()
    with open(globals.TOKENS_FILE, "w", encoding="utf-8") as f:
        pass
    logger.info(f"Token count: {len(globals.token_list)}, Error token count: {len(globals.error_token_list)}")
    tokens_count = len(set(globals.token_list) - set(globals.error_token_list))
    return {"status": "success", "tokens_count": tokens_count}


@app.post(f"/{api_prefix}/tokens/error" if api_prefix else "/tokens/error")
async def error_tokens():
    error_tokens_list = list(set(globals.error_token_list))
    return {"status": "success", "error_tokens": error_tokens_list}


@app.get(f"/{api_prefix}/tokens/add/{{token}}" if api_prefix else "/tokens/add/{token}")
async def add_token(token: str):
    if token.strip() and not token.startswith("#"):
        globals.token_list.append(token.strip())
        with open(globals.TOKENS_FILE, "a", encoding="utf-8") as f:
            f.write(token.strip() + "\n")
    logger.info(f"Token count: {len(globals.token_list)}, Error token count: {len(globals.error_token_list)}")
    tokens_count = len(set(globals.token_list) - set(globals.error_token_list))
    return {"status": "success", "tokens_count": tokens_count}


@app.post(f"/{api_prefix}/seed_tokens/clear" if api_prefix else "/seed_tokens/clear")
async def clear_seed_tokens():
    globals.seed_map.clear()
    globals.conversation_map.clear()
    with open(globals.SEED_MAP_FILE, "w", encoding="utf-8") as f:
        f.write("{}")
    with open(globals.CONVERSATION_MAP_FILE, "w", encoding="utf-8") as f:
        f.write("{}")
    logger.info(f"Seed token count: {len(globals.seed_map)}")
    return {"status": "success", "seed_tokens_count": len(globals.seed_map)}
