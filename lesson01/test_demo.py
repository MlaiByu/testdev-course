# -*- coding: utf-8 -*-
"""
第 1 课示例：你的第一个 pytest 测试

运行方式（在本文件夹打开终端执行）：
    py -m pip install pytest
    py -m pytest test_demo.py -v
"""


def add(a, b):
    """被测函数：加法"""
    return a + b


# ---------- 以下是用 add 的测试用例 ----------

def test_add_normal():
    """正常场景（有效等价类的代表）：1 + 1 = 2"""
    assert add(1, 1) == 2


def test_add_negative():
    """异常场景（无效等价类的代表）：负数相加也要正确"""
    assert add(-1, -2) == -3


def test_add_zero():
    """边界值：0 是加法的天然边界"""
    assert add(0, 0) == 0


# 作业：模仿上面的写法，为 str.upper() 写 2 个测试函数
# 其中故意让 1 个失败，观察 pytest 的报错输出长什么样
def test_str_upper():
    """测试 str.upper() 方法"""
    assert "hello".upper() == "HELL1"  # 故意写错，应该是 "HELLO"


def test_str_upper_with_space():
    """测试 str.upper() 方法（包含空格）"""
    assert "hello world".upper() == "HELLO WORLD"
