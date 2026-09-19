import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

# === 配置你的邮箱（毕设展示时填入真实的）===
MAIL_HOST = "smtp.qq.com"
MAIL_USER = "3305134074@qq.com"
MAIL_PASS = "exkjeblctvptdaje"  # 不是登录密码，是SMTP授权码

def send_email_alert(subject, content):
    """
    发送邮件通知
    """
    sender = MAIL_USER
    receivers = [MAIL_USER]  # 默认发给自己（你也可以改成列表）

    # 邮件内容
    message = MIMEText(content, "plain", "utf-8")
    message["From"] = formataddr((str(Header("网络监控平台", "utf-8")), sender))
    message["To"] = formataddr((str(Header("管理员", "utf-8")), receivers[0]))
    message["Subject"] = Header(subject, "utf-8")

    smtpObj = None
    try:
        # ✅ QQ 邮箱推荐：465 SSL 直连
        smtpObj = smtplib.SMTP_SSL(MAIL_HOST, 465, timeout=10)
        smtpObj.ehlo()

        # 登录 + 发送
        smtpObj.login(MAIL_USER, MAIL_PASS)
        smtpObj.sendmail(sender, receivers, message.as_string())

        print("邮件发送成功")
        return True
    except Exception as e:
        print(f"无法发送邮件: {e}")
        return False
    finally:
        if smtpObj is not None:
            try:
                smtpObj.quit()
            except Exception:
                pass
