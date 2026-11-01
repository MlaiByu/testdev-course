"""协议层用例：不看业务字段，只看 HTTP 规范有没有被遵守

这一组用例的价值不在「功能对不对」，而在「合不合规范」。
功能测试通常只看 body，协议测试只看头和状态码语义——
很多真实缺陷只在后者暴露（比如缺 WWW-Authenticate、Content-Type 不对、缓存头缺失）。
"""
import allure
import pytest

pytestmark = allure.feature("协议规范")


@allure.story("RFC 7235")
@allure.title("401 必须带 WWW-Authenticate 头（当前缺失 → 已知缺陷）")
@pytest.mark.xfail(
    strict=True,
    reason="已知缺陷：get_current_user 抛 BizException 时未附 WWW-Authenticate 头",
)
def test_401_has_www_authenticate_header(api, base_url):
    """RFC 7235 §3.1：返回 401 的服务器 MUST 发送 WWW-Authenticate

    它告诉客户端「该用哪种认证方案」（这里是 Bearer）。
    缺了它，curl --anyauth、部分 HTTP SDK 无法自动选择认证方式，
    只能靠人肉翻文档。

    strict=True 的含义：如果哪天这个头补上了，用例会从 xfail 变成 XPASS，
    **pytest 会把它判为失败**——逼你回来把 xfail 标记删掉。缺陷修了要有人知道。
    """
    r = api.get(f"{base_url}/api/v1/auth/me")
    assert r.status_code == 401, "未登录访问受保护接口应 401"
    assert "WWW-Authenticate" in r.headers, (
        "401 响应缺 WWW-Authenticate 头（RFC 7235 §3.1 MUST），"
        f"现有响应头: {dict(r.headers)}"
    )


@allure.story("媒体类型")
@allure.title("JSON 接口的 Content-Type 必须是 application/json")
def test_json_content_type(api, base_url):
    """错误页最容易破这条：网关返回 HTML 502 页时，Content-Type 会变成 text/html

    断言响应头能让「请求打到了错误的服务器」这类问题立刻现形，
    否则你只会看到 body 解析失败，以为是业务 bug。
    """
    r = api.get(f"{base_url}/api/v1/products", params={"size": 1})
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("application/json")


@allure.story("追踪")
@allure.title("每个响应都要带 x-trace-id，便于和日志对齐")
def test_response_has_trace_id(api, base_url):
    """线上排障靠它：用户报错时给一个 trace id，你能捞出那次请求的全部日志"""
    r = api.get(f"{base_url}/api/v1/products", params={"size": 1})
    assert "x-trace-id" in r.headers
    assert len(r.headers["x-trace-id"]) >= 8, "trace id 太短，碰撞概率高"
