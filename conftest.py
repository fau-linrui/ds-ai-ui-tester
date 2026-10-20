"""pytest 公共配置。

两个约定：
1. base_url 从环境变量 BASE_URL 读，默认本地演示站点。
2. AUTH_STATE 指向 storage_state 文件时，自动注入到 browser context（登录态复用）。
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    """站点根地址。用例里的相对路径都拼在它后面。"""
    return os.environ.get("BASE_URL", "http://127.0.0.1:3000").rstrip("/")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict) -> dict:
    """复用登录态：AUTH_STATE=auth.json 时注入 storage_state。"""
    args = dict(browser_context_args)
    state = os.environ.get("AUTH_STATE")
    if state:
        args["storage_state"] = state
    return args
