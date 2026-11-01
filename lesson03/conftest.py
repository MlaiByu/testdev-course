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


def new_session():
    """trust_env=False 忽略系统代理（本机被代理接管会返回 502 假象）；
    不设全局 Content-Type——requests 会按 json=/data=/files= 自动决定，
    写死会让 data= 请求的头撒谎（头说 JSON，body 是表单）。"""
    s = requests.Session()      # requests.Session() 里有 cookiejar、连接池、默认头
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
def user_factory(base_url):
    """工厂 fixture：想造几个用户就造几个

    订单组需要「对手」账号（比如买光库存、访问别人的订单），
    而普通 fixture 一条用例只能注入一次，所以要用工厂。
    每个用户自带独立会话，互不影响。
    """
    def _make(prefix="u"):
        s = new_session()
        email = make_email(prefix)
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

    return _make


@pytest.fixture()
def logged_user(user_factory):
    """已登录用户：返回 {"email","password","id","token","api"}

    内部调用工厂，自己开独立会话（不复用用例注入的 api），
    否则 api 里会残留 cookie，导致「测 Bearer 头」的用例实际走 cookie 路径。
    """
    return user_factory()


@pytest.fixture()
def auth_headers(logged_user):
    """每条用例一份新字典，防用例互改；身份来自 logged_user 的独立会话"""
    return {"Authorization": f"Bearer {logged_user['token']}"}


@pytest.fixture()
def a_sku(api, base_url):
    """挑一个「在售且有库存」的 SKU——订单组的前置基石

    不写死 sku_id：数据库会被别的用例改（下架、扣库存），写死等于把用例
    焊死在某条数据上，别人一改商品就红。
    """
    items = api.get(f"{base_url}/api/v1/products", params={"size": 3}).json()["data"]["items"]
    assert items, "商品列表为空，无法构造下单场景"

    detail = api.get(f"{base_url}/api/v1/products/{items[0]['id']}").json()["data"]
    skus = [s for s in detail["skus"] if s["is_active"] and s["stock"] > 0]
    assert skus, f"商品 {items[0]['id']} 没有可售 SKU"
    sku = dict(skus[0])
    sku["product_id"] = detail["id"]        # 方便用例回查实时库存
    return sku


@pytest.fixture()
def order_ready(logged_user, base_url, a_sku):
    """下单前置：加购 2 件 + 建收货地址

    返回 {"api", "cart_item_id", "address_id", "sku", "quantity"}。
    加购和地址都是「用户私有数据」，所以每条用例各造一份，天然隔离——
    不需要写 teardown 去清理，也不怕顺序变化。
    """
    api = logged_user["api"]
    quantity = 2

    added = api.post(f"{base_url}/api/v1/cart", json={"sku_id": a_sku["id"], "quantity": quantity})
    assert added.status_code == 200, f"前置加购失败，场景没建立: {added.text}"

    addr = api.post(
        f"{base_url}/api/v1/addresses",
        json={
            "receiver": "张三",
            "phone": make_phone(),
            "province": "广东省",
            "city": "深圳市",
            "district": "南山区",
            "detail": "科技园 1 号",
        },
    )
    assert addr.status_code == 200, f"前置建地址失败，场景没建立: {addr.text}"

    return {
        "api": api,
        "cart_item_id": added.json()["data"]["id"],
        "address_id": addr.json()["data"]["id"],
        "sku": a_sku,
        "quantity": quantity,
    }
