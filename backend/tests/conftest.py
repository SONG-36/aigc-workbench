"""离线回归使用模拟费率日期，并禁止意外建立真实模型网络连接。"""
from datetime import date
import socket

import pytest


@pytest.fixture(autouse=True)
def offline_model_environment(monkeypatch):
    # 不改生产费率或绕过生产门禁；让 Fake 模型测试不随自然日期失效。
    # 测试过期校验的用例仍可自行覆盖这些常量。
    monkeypatch.setattr('aigc_workbench.c06_generation.PRICING_CHECKED', date.today().isoformat())
    monkeypatch.setattr('aigc_workbench.c01_model.PRICING_CHECKED', date.today().isoformat())
    monkeypatch.setattr('aigc_workbench.c01_model.TERRA_PRICING_CHECKED', date.today().isoformat())
    def forbidden(*args, **kwargs):
        raise AssertionError('OFFLINE_TEST_NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
