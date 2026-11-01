"""测试基座：常量 + 工具函数 + fixture

作用域原则：贵的、不变的 → session；会变的、有唯一约束的 → function
认证契约：登录下发 httpOnly Cookie(access_token)，响应体无 token；
         服务端优先 cookie，其次兼容 Authorization: Bearer
"""
import base64
import json
import uuid
from http.cookies import SimpleCookie

import pytest
import requests

BASE_URL = "http://127.0.0.1:8000"
DEFAULT_PASSWORD = "Abc12345"
COOKIE_NAME = "access_token"


def make_email(prefix="stu"):
    """唯一邮箱：注册类用例用它，永远不撞唯一约束"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}@xuanwu.com"


def make_phone():
    """唯一手机号："13" 保证第二位合法，共 11 位"""
    return "13" + str(uuid.uuid4().int)[:9]


def new_session() -> requests.Session:
    """trust_env=False 忽略系统代理（本机被代理接管会返回 502 假象）；
    不设全局 Content-Type——requests 会按 json=/data=/files= 自动决定，
    写死会让 data= 请求的头撒谎（头说 JSON，body 是表单）。"""
    s = requests.Session()
    s.trust_env = False
    return s


def cookie_of(resp, name=COOKIE_NAME):
    """解析 Set-Cookie 返回 Morsel，可按属性名取：c["httponly"] / c["samesite"] / c.value

    比字符串 in 更严谨：属性顺序无关，能区分「属性不存在」与「值为空」。
    限制：多个同名 Set-Cookie 会被 requests 用逗号合并，此时改用 resp.raw.headers.getlist。
    """
    raw = resp.headers.get("Set-Cookie", "")
    jar = SimpleCookie()
    jar.load(raw)
    assert name in jar, f"响应没有下发 {name} cookie，原始头: {raw!r}"
    return jar[name]


def decode_jwt(token):
    """拆成 {"header", "payload"}——只读明文，不验签

    payload 是 base64url 明文：JWT 保证完整性（改不了签名），不保证机密性，
    所以 email/phone/密码这类敏感字段绝不能放进去。
    """
    parts = token.split(".")
    assert len(parts) == 3, f"不是 JWT 三段结构: {token[:40]}..."

    def load(seg):
        return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))

    return {"header": load(parts[0]), "payload": load(parts[1])}


@pytest.fixture(scope="session")
def base_url():
    """环境地址：换环境只改这一处"""
    return BASE_URL


@pytest.fixture()
def api():
    """干净会话，function 级：用完即毁，cookie 不外泄

    只保留这一个客户端 fixture——共享 session 省不到 1ms，
    却会让登录态漏进后续用例（共享可变状态的经典事故）。
    """
    s = new_session()
    yield s
    s.close()


@pytest.fixture()
def unique_email():
    return make_email()


@pytest.fixture()
def unique_phone():
    return make_phone()


@pytest.fixture()
def logged_user(base_url):
    """已登录用户：返回 {"email","password","id","token","api"}

    自己开一个独立会话（不复用用例注入的 api），否则 api 里会残留 cookie，
    导致「测 Bearer 头」的用例实际走的是 cookie 路径——绿了但测错了东西。
    """
    s = new_session()
    email = make_email("u")
    reg = s.post(f"{base_url}/api/v1/auth/register",
                 json={"email": email, "password": DEFAULT_PASSWORD})
    assert reg.status_code == 201, f"前置注册失败，场景没建立: {reg.text}"

    r = s.post(f"{base_url}/api/v1/auth/login",
               json={"account": email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200, f"前置登录失败，场景没建立: {r.text}"

    return {
        "email": email,
        "password": DEFAULT_PASSWORD,
        "id": r.json()["data"]["user"]["id"],
        "token": cookie_of(r).value,
        "api": s,
    }


@pytest.fixture()
def auth_headers(logged_user):
    """每条用例一份新字典，防用例互改；身份来自 logged_user 的独立会话"""
    return {"Authorization": f"Bearer {logged_user['token']}"}
