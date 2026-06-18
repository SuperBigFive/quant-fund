import requests


def send_wechat(title, content, token, topic=""):
    url = "http://www.pushplus.plus/send"
    data = {
        "token": token,
        "title": title,
        "content": content,
        "template": "txt",
    }
    if topic:
        data["topic"] = topic

    try:
        resp = requests.post(url, json=data, timeout=30)
        result = resp.json()
        code = result.get("code")

        if code == 200:
            return True

        # 群组不存在时降级为无 topic 重试
        if code == 999 and topic and "群组" in str(result.get("data", "")):
            print(f"  群组「{topic}」不存在，降级为普通推送重试...")
            del data["topic"]
            resp2 = requests.post(url, json=data, timeout=30)
            result2 = resp2.json()
            if result2.get("code") == 200:
                return True
            print(f"  推送失败: {result2.get('msg', '未知错误')} (降级重试后)")
            return False

        print(f"  推送失败: {result.get('msg', '未知错误')} (code={code})")
        return False
    except Exception as e:
        print(f"  推送异常: {e}")
        return False