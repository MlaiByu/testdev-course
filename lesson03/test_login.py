"""登录接口用例（API08-API12）

断言层次：status_code → body.code → 业务字段 → 契约 → 凭证内容 → 活性
规矩：每条用例自给自足，不依赖别的用例先跑过。

认证契约（2026-11-01 变更）：
- 登录成功后凭证放在 httpOnly Cookie（access_token）里，响应体不再返回 token
- 服务端 _extract_token 优先 cookie，其次兼容 Authorization: Bearer（供脚本调试）
- 所以「登录成功」的断言也从「取 token」变成「验 Set-Cookie + 验 cookie 能用」
"""
import allure

from conftest import COOKIE_NAME, DEFAULT_PASSWORD, cookie_of, decode_jwt, make_email

pytestmark = allure.feature("认证")


@allure.story("登录")
@allure.title("正常登录：200 + httpOnly cookie + 凭证内容正确 + cookie 可用")
def test_login_success(api, base_url, unique_email):
    """API08 正常登录：200 + 下发 httpOnly cookie + 凭证内容正确 + cookie 可用"""
    # ① 前置：注册一个我掌握密码的账号（并证明场景已抵达）
    reg = api.post(
        f"{base_url}/api/v1/auth/register",
        json={"email": unique_email, "password": DEFAULT_PASSWORD},
    )
    assert reg.status_code == 201, f"前置注册失败，场景没建立: {reg.text}"

    # ② 动作：同一个邮箱 + 正确的密码（注意字段名是 account，不是 email）
    r = api.post(
        f"{base_url}/api/v1/auth/login",
        json={"account": unique_email, "password": DEFAULT_PASSWORD},
    )

    # ③ 状态层
    assert r.status_code == 200
    # ④ 业务层
    body = r.json()
    assert body["code"] == 0

    # ⑤ 业务字段层
    assert body["data"]["expires_in"] == 86400
    user = body["data"]["user"]
    assert user["email"] == unique_email
    assert user["role"] == "customer"
    assert user["is_admin"] is False
    assert "hashed_password" not in user

    # ⑥ 契约层：凭证不得出现在响应体（本次变更的核心）
    assert "token" not in body["data"]

    # ⑦ Cookie 契约层：凭证必须在 httpOnly cookie 里（按属性断言，不靠字符串匹配）
    c = cookie_of(r)
    assert c["httponly"] is True, "缺 HttpOnly：JS 可读，XSS 能偷走凭证"
    assert c["samesite"] == "lax", "缺 SameSite=lax：跨站请求会带上凭证，有 CSRF 风险"
    assert c["max-age"] == "86400", "有效期与 expires_in 不一致"
    assert c.value.count(".") == 2, "cookie 里的凭证不是 JWT 三段结构"

    # ⑧ 凭证内容层：JWT payload 是明文，既要验内容，也要验「没多塞东西」
    jwt = decode_jwt(c.value)
    assert jwt["header"]["alg"] == "HS256", "算法被降级：alg=none 伪造会绕过验签"
    assert jwt["payload"]["sub"] == str(user["id"]), "凭证绑定的身份与登录用户不一致"
    assert jwt["payload"]["is_admin"] is False, "普通用户凭证里不许出现管理员标记"
    assert jwt["payload"]["exp"] - jwt["payload"]["iat"] == 86400, "有效期与 expires_in 不一致"
    for field in ("email", "phone", "password", "hashed_password"):
        assert field not in jwt["payload"], f"敏感字段 {field} 泄漏进了 JWT payload"

    # ⑨ 活性层：cookie 由 Session 自动保管并回传，这才证明凭证可用
    me = api.get(f"{base_url}/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["email"] == unique_email


@allure.story("登录")
@allure.title("密码错误：401 + 40100 + 与「账号不存在」一字不差的文案")
def test_login_wrong_password(api, base_url, unique_email):
    """API09 密码错误：401 + 40100 + 与「账号不存在」一字不差的文案（反枚举）"""
    # ① 前置：账号必须真实存在，否则测的是「账号不存在」而不是「密码错误」
    reg = api.post(
        f"{base_url}/api/v1/auth/register",
        json={"email": unique_email, "password": DEFAULT_PASSWORD},
    )
    assert reg.status_code == 201, f"前置注册失败，场景没建立: {reg.text}"

    # ② 动作：同一个邮箱 + 格式合法但内容错误的口令
    r = api.post(
        f"{base_url}/api/v1/auth/login",
        json={"account": unique_email, "password": "WrongPass1"},
    )

    # ③ 状态层：必须是 401（404 会泄漏用户不存在，403 是已登录但无权限）
    assert r.status_code == 401
    # ④ 业务层
    body = r.json()
    assert body["code"] == 40100
    # ⑤ 安全层：文案必须与「账号不存在」完全一致，否则可批量探测邮箱是否注册
    assert body["message"] == "账号或密码错误"
    assert body["data"] is None
    # ⑥ 契约层：失败时也不许下发凭证
    assert COOKIE_NAME not in r.cookies


@allure.story("登录")
@allure.title("账号不存在：401 + 40100（前置什么都不做，验证系统不多嘴）")
def test_login_account_not_exist(api, base_url):
    """API10 账号不存在：401 + 40100 + 与「密码错误」一字不差的文案（反枚举）

    前置是「什么都不做」——被注册过的账号才叫前置，
    这条测的正是「用户不存在时系统会不会多嘴」。
    """
    ghost = make_email("ghost")          # 造一个绝不会被注册的邮箱
    r = api.post(
        f"{base_url}/api/v1/auth/login",
        json={"account": ghost, "password": DEFAULT_PASSWORD},
    )
    # 状态层：必须是 401，不能是 404 —— 状态码本身也能泄漏「用户不存在」
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == 40100
    assert body["message"] == "账号或密码错误"
    assert body["data"] is None


@allure.story("鉴权")
@allure.title("Bearer 头兼容路径：服务端 _extract_token 支持，防止被误删")
def test_auth_header_compat(api, base_url, auth_headers):
    """API11 Bearer 头兼容路径：服务端 _extract_token 明确支持，防止被误删

    这条必须用「没有 cookie 的会话」+ Bearer 头，否则服务端 cookie 优先，
    测到的就是 cookie 路径而不是头路径——绿了但测错了东西。
    api 是 function 级的干净会话，auth_headers 来自另一个独立会话，刚好隔离。
    """
    r = api.get(f"{base_url}/api/v1/auth/me", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["code"] == 0


@allure.story("登出")
@allure.title("退出登录：清除 cookie，之后访问受保护接口回到未登录态")
def test_logout_clears_cookie(logged_user, base_url):
    """API12 退出登录：清除 cookie，之后访问受保护接口回到未登录态

    前置交给 logged_user fixture：注册+登录 6 行重复代码不用再抄一遍，
    但断言全部留在用例里——前置可以抽象，断言不行。
    """
    api = logged_user["api"]              # 已带 cookie 的会话
    assert api.get(f"{base_url}/api/v1/auth/me").status_code == 200

    # ① 动作：退出
    r = api.post(f"{base_url}/api/v1/auth/logout")
    assert r.status_code == 200
    assert r.json()["code"] == 0

    # ② 契约层：用 Max-Age=0 + 空值 让客户端删掉 cookie
    c = cookie_of(r)
    assert c["max-age"] == "0", "退出登录没有让 cookie 立即过期"
    assert c.value == "", "退出登录没有把 cookie 值清空"

    # ③ 活性层：退出后必须回到未登录态（这条才真正验证「凭证已失效」）
    me = api.get(f"{base_url}/api/v1/auth/me")
    assert me.status_code == 401
    assert me.json()["code"] == 40100
