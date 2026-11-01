"""第 3 课 · 注册接口用例（API01-API07）→ pytest 代码

断言四层：status_code → body.code → 业务字段 → 副作用（DB 断言留到进阶课）
规矩：每条用例自给自足，不依赖别的用例先跑过。
"""
import allure
import pytest

from conftest import DEFAULT_PASSWORD, make_email

pytestmark = allure.feature("认证")


@allure.story("注册")
@allure.title("正常注册：201 + code=0 + 邮箱转小写 + 响应无敏感字段")
def test_register_success(api, base_url, unique_email, unique_phone):
    """API01 正常注册：201 + code=0 + 邮箱转小写 + 响应无敏感字段"""
    r = api.post(
        f"{base_url}/api/v1/auth/register",
        json={
            "email": unique_email.upper(),
            "phone": unique_phone,
            "password": DEFAULT_PASSWORD,
            "nickname": "测试号",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["email"] == unique_email
    assert body["data"]["phone"] == unique_phone
    assert "hashed_password" not in body["data"]
            

@allure.story("注册")
@allure.title("非法密码（长度/强度/类型）：400 + 40001，errors 指明字段")
@pytest.mark.parametrize(
    "password",
    [
        "Abc1234",            # 7 位：下边界外
        "Aa1" + "a" * 62,     # 65 位：上边界外
        "abcdefgh",           # 纯字母：强度不足
        12345678,             # 类型混淆：数字
    ],
)
def test_register_invalid_password(api, base_url, unique_email, password):
    """API02/03 + 强度/类型：参数错误统一 400 + 40001，errors 指明字段"""
    r = api.post(
        f"{base_url}/api/v1/auth/register",
        json={"email": unique_email, "password": password},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == 40001
    fields = [e["field"] for e in body["data"]["errors"]]
    assert "password" in fields


@allure.story("注册")
@allure.title("重复邮箱：409 + 40900（前置在用例内自造）")
def test_register_duplicate_email(api, base_url):
    """API06 重复注册：409 + 40900（前置在用例内部自造，不依赖 API01）"""
    email = make_email("dup")
    payload = {"email": email, "password": DEFAULT_PASSWORD}
    first = api.post(f"{base_url}/api/v1/auth/register", json=payload)
    assert first.status_code == 201
    second = api.post(f"{base_url}/api/v1/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["code"] == 40900


@allure.story("注册")
@allure.title("XSS 昵称：原样存为字符串，不执行、不破坏 JSON 结构")
def test_register_xss_nickname(api, base_url, unique_email):
    """API07 XSS：nickname 原样存为字符串，不执行、不破坏 JSON 结构"""
    r = api.post(
        f"{base_url}/api/v1/auth/register",
        json={
            "email": unique_email,
            "password": DEFAULT_PASSWORD,
            "nickname": "<img src=x>",
        },
    )
    assert r.status_code == 201
    assert r.json()["data"]["nickname"] == "<img src=x>"


@allure.story("注册")
@allure.title("非法手机号：400 + 40001，errors 必须指到 phone 字段")
@pytest.mark.parametrize(
    "phone",
    [
        "1280013800",      # 10 位：少一位
        "138001380",       # 9 位：少两位
        "138001380000",    # 12 位：多一位
        "1380013800a",     # 含字母
        "22800138000",     # 11 位但第二位是 2，不满足 1[3-9]
    ],
)
def test_register_invalid_phone(api, base_url, unique_email, phone):
    """API04 手机号格式非法：契约要求 ^1[3-9]\\d{9}$

    参数化而不是复制 5 遍：数据不同、逻辑相同，正是参数化的用武之地。
    断言 errors 里的 field 必须是 phone —— 只断言 400 不够，
    因为「400 但指错了字段」对前端毫无帮助。
    """
    r = api.post(
        f"{base_url}/api/v1/auth/register",
        json={"email": unique_email, "password": DEFAULT_PASSWORD, "phone": phone},
    )
    assert r.status_code == 400, f"非法手机号 {phone!r} 应被拒，实际 {r.status_code}"
    body = r.json()
    assert body["code"] == 40001
    assert body["message"] == "参数校验失败"
    fields = [e["field"] for e in body["data"]["errors"]]
    assert "phone" in fields, f"错误没指到 phone 字段: {body['data']['errors']}"


@allure.story("注册")
@allure.title("空串手机号：归一为 null（实测行为，契约未记载）")
def test_register_empty_phone_normalized_to_null(api, base_url, unique_email):
    """空串 "" 被当成「没填」，落库为 null

    契约只写了 phone 的格式 ^1[3-9]\\d{9}$ 且非必填，没写「空串怎么处理」。
    实测：空串注册成功且存为 null。这条用例把**未记载的行为**钉住，
    将来实现改成「空串报 40001」时，这里会红，提醒你契约该更新了。
    """
    r = api.post(
        f"{base_url}/api/v1/auth/register",
        json={"email": unique_email, "password": DEFAULT_PASSWORD, "phone": ""},
    )
    assert r.status_code == 201, f"空串手机号的处理方式变了: {r.text}"
    assert r.json()["data"]["phone"] is None, "空串应被归一为 null，不能存成空字符串"
