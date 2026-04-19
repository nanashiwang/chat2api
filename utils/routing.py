import json
from datetime import datetime, timezone

import utils.globals as globals
from utils.Logger import logger


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def mask_token(token):
    if not token:
        return ""
    if len(token) <= 12:
        return token
    return f"{token[:6]}...{token[-4:]}"


def get_routing_config():
    config = globals.routing_config or {}
    config.setdefault("proxies", [])
    config.setdefault("groups", [])
    config.setdefault("bindings", {})
    config.setdefault("updated_at", None)
    return config


def save_routing_config(config):
    config["updated_at"] = utc_now()
    globals.routing_config = config
    with open(globals.ROUTING_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def resolve_group_name(config, proxy_name, proxy_url):
    for group in config.get("groups", []):
        if group.get("proxy_url") == proxy_url:
            return group.get("name")
    return f"Group {proxy_name}"


def build_group_assignments(tokens, proxies, group_size):
    group_size = max(int(group_size or 1), 1)
    bindings = {}
    groups = []
    normalized_proxies = []

    for index, proxy in enumerate(proxies):
        proxy_name = (proxy.get("name") or f"IP-{index + 1}").strip()
        proxy_url = (proxy.get("proxy_url") or "").strip()
        if not proxy_url:
            continue
        normalized_proxies.append({
            "id": f"proxy-{index + 1}",
            "name": proxy_name,
            "proxy_url": proxy_url,
        })

    for proxy_index, proxy in enumerate(normalized_proxies):
        start = proxy_index * group_size
        end = min(start + group_size, len(tokens))
        group_tokens = tokens[start:end]
        if not group_tokens:
            break
        group_name = f"Group {chr(65 + proxy_index)}"
        groups.append({
            "id": f"group-{proxy_index + 1}",
            "name": group_name,
            "proxy_name": proxy["name"],
            "proxy_url": proxy["proxy_url"],
            "size": len(group_tokens),
            "strategy": "fixed",
            "status": "enabled",
        })
        for token in group_tokens:
            bindings[token] = {
                "group": group_name,
                "proxy_name": proxy["name"],
                "proxy_url": proxy["proxy_url"],
                "updated_at": utc_now(),
            }

    return {
        "proxies": normalized_proxies,
        "groups": groups,
        "bindings": bindings,
    }


def sync_bindings_to_fp(bindings):
    changed = False
    for token, binding in bindings.items():
        if not token:
            continue
        fp = globals.fp_map.get(token, {})
        proxy_url = binding.get("proxy_url")
        if fp.get("proxy_url") != proxy_url:
            fp["proxy_url"] = proxy_url
            changed = True
        if binding.get("group"):
            fp["group"] = binding["group"]
        if binding.get("proxy_name"):
            fp["proxy_name"] = binding["proxy_name"]
        fp["updated_at"] = binding.get("updated_at", utc_now())
        globals.fp_map[token] = fp
    if changed:
        logger.info("Routing bindings synced to fp_map.json")
    with open(globals.FP_FILE, "w", encoding="utf-8") as f:
        json.dump(globals.fp_map, f, indent=2, ensure_ascii=False)


def update_single_binding(token, proxy_name, proxy_url, group_name=None):
    config = get_routing_config()
    existing_proxy = next((item for item in config.get("proxies", []) if item.get("proxy_url") == proxy_url), None)
    if not existing_proxy:
        config.setdefault("proxies", []).append({
            "id": f"proxy-{len(config.get('proxies', [])) + 1}",
            "name": proxy_name,
            "proxy_url": proxy_url,
        })

    if not group_name:
        group_name = resolve_group_name(config, proxy_name, proxy_url)

    config.setdefault("bindings", {})[token] = {
        "group": group_name,
        "proxy_name": proxy_name,
        "proxy_url": proxy_url,
        "updated_at": utc_now(),
    }
    save_routing_config(config)
    sync_bindings_to_fp({token: config["bindings"][token]})
    return config["bindings"][token]


def get_bound_proxy(req_token):
    binding = get_routing_config().get("bindings", {}).get(req_token)
    if binding:
        return binding.get("proxy_url")
    return None


def get_dashboard_payload():
    config = get_routing_config()
    bindings = config.get("bindings", {})
    proxies = config.get("proxies", [])
    tokens = list(globals.token_list)
    error_tokens = set(globals.error_token_list)
    active_tokens = len([token for token in tokens if token not in error_tokens])
    grouped_rules = {}

    proxy_stats = []
    for proxy in proxies:
        matched_tokens = [token for token, binding in bindings.items() if binding.get("proxy_url") == proxy["proxy_url"]]
        bad_count = len([token for token in matched_tokens if token in error_tokens])
        rule_name = None
        if matched_tokens:
            rule_name = bindings[matched_tokens[0]].get("group")
            grouped_rules[rule_name or proxy["name"]] = {
                "name": rule_name or proxy["name"],
                "proxy_name": proxy["name"],
                "proxy_url": proxy["proxy_url"],
                "size": len(matched_tokens),
                "strategy": "fixed",
                "status": "enabled",
            }
        proxy_stats.append({
            "name": proxy["name"],
            "proxy_url": proxy["proxy_url"],
            "accounts": len(matched_tokens),
            "ok": len(matched_tokens) - bad_count,
            "bad": bad_count,
            "group": rule_name or "-",
        })

    accounts = []
    for index, token in enumerate(tokens, start=1):
        binding = bindings.get(token, {})
        status = "异常" if token in error_tokens else "正常"
        proxy_name = binding.get("proxy_name", "-")
        group_name = binding.get("group", "-")
        accounts.append({
            "id": f"acct-{index:03d}",
            "token": token,
            "token_masked": mask_token(token),
            "status": status,
            "proxy_name": proxy_name,
            "group": group_name,
            "updated_at": binding.get("updated_at") or globals.fp_map.get(token, {}).get("updated_at") or "-",
        })

    alerts = []
    if error_tokens:
        alerts.append(f"当前有 {len(error_tokens)} 个异常账号，建议优先检查刷新状态。")
    unbound_count = max(len(tokens) - len(bindings), 0)
    if unbound_count:
        alerts.append(f"还有 {unbound_count} 个账号未绑定固定代理。")
    if not alerts:
        alerts.append("当前未发现异常告警。")

    return {
        "summary": {
            "accounts_total": len(tokens),
            "accounts_ok": active_tokens,
            "accounts_bad": len(error_tokens),
            "proxy_total": len(proxies),
            "group_total": len(grouped_rules),
            "bound_total": len(bindings),
        },
        "ip_cards": proxy_stats,
        "accounts": accounts,
        "rules": list(grouped_rules.values()),
        "alerts": alerts,
        "updated_at": config.get("updated_at"),
    }
