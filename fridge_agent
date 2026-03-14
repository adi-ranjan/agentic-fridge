import boto3
import json
import requests
import time
import uuid
from datetime import datetime, timedelta, timezone

db = boto3.resource('dynamodb').Table('FridgeState')
sm = boto3.client('secretsmanager')


# -------- GET SECRETS --------
def get_secrets():
    res = sm.get_secret_value(SecretId='FridgeAgentSecrets')
    return json.loads(res['SecretString'])


# -------- GET PINE TOKEN --------
def get_token(sec):

    url = "https://pluraluat.v2.pinepg.in/api/auth/v1/token"

    headers = {
        "accept": "application/json",
        "content-type": "application/json"
    }

    payload = {
        "client_id": sec["PINE_CLIENT_ID"],
        "client_secret": sec["PINE_SECRET"],
        "grant_type": "client_credentials"
    }

    r = requests.post(url, headers=headers, json=payload, timeout=10)

    print("TOKEN RESPONSE:", r.text)

    data = r.json()

    if "access_token" not in data:
        raise Exception(f"Token Error: {data}")

    return data["access_token"]


# -------- CREATE PAYMENT LINK --------
def create_payment_link(sec, token, order, user_phone):

    order_id = f"FRID{int(time.time())}"

    expire_time = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).strftime("%Y-%m-%dT%H:%MZ")

    payload = {
        "merchant_id": str(sec["PINE_MERCH_ID"]),
        "order_id": order_id,
        "merchant_payment_link_reference": order_id,
        "description": f"Restock_{order['name']}",

        "amount": {
            "value": int(order["price"] * 100),
            "currency": "INR"
        },

        "expire_by": expire_time,

        "allowed_payment_methods": [
            "UPI",
            "CARD",
            "NETBANKING"
        ],

        "customer": {
            "mobile_number": str(user_phone)[-10:],
            "country_code": "91",
            "first_name": "Customer",
            "last_name": "Fridge",
            "email_id": "customer@example.com",
            "customer_id": str(user_phone)[-10:]
        },

        "product_details": [
            {
                "product_code": order["name"],
                "product_amount": {
                    "currency": "INR",
                    "value": int(order["price"] * 100)
                },
                "product_coupon_discount_amount": {
                    "currency": "INR",
                    "value": 0
                }
            }
        ],

        "part_payment": False,

        "callback_url": "https://your-api-url/payment-success",
        "failure_callback_url": "https://your-api-url/payment-failure",

        "merchant_metadata": {
            "source": "fridge_agent"
        }
    }

    timestamp = datetime.now(timezone.utc).isoformat()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Request-ID": str(uuid.uuid4()),
        "Request-Timestamp": timestamp
    }

    url = "https://pluraluat.v2.pinepg.in/api/pay/v1/paymentlink"

    print("PAYLOAD:", json.dumps(payload))

    r = requests.post(url, headers=headers, json=payload, timeout=10)

    print("STATUS:", r.status_code)
    print("RESPONSE:", r.text)

    data = r.json()

    return data.get("payment_link")


# -------- SEND WHATSAPP MESSAGE --------
def send_whatsapp(sec, user_phone, message):

    url = f"https://graph.facebook.com/v22.0/{sec['WHATSAPP_PHONE_ID']}/messages"

    headers = {
        "Authorization": f"Bearer {sec['WHATSAPP_TOKEN']}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": user_phone,
        "text": {"body": message}
    }

    requests.post(url, headers=headers, json=payload)


# -------- MAIN HANDLER --------
def lambda_handler(event, context):

    sec = get_secrets()

    print("EVENT:", json.dumps(event))

    # -------- WHATSAPP VERIFICATION --------
    if event.get("httpMethod") == "GET":

        params = event.get("queryStringParameters", {})

        if params.get("hub.verify_token") == sec["VERIFY_TOKEN"]:
            return {
                "statusCode": 200,
                "body": params.get("hub.challenge")
            }

        return {"statusCode": 403}


    # -------- HANDLE MESSAGE --------
    try:

        body = event.get("body", "{}")

        if isinstance(body, str):
            body = json.loads(body)

        value = body["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return {"statusCode": 200}

        message = value["messages"][0]

        user_msg = message["text"]["body"].upper().strip()
        user_phone = message["from"]

        print("USER MSG:", user_msg)

        if "YES" not in user_msg:
            return {"statusCode": 200}

        # -------- GET ORDER --------
        res = db.get_item(Key={"id": "pending_order"})

        if "Item" not in res:
            raise Exception("No pending order")

        order = res["Item"]

        # -------- GET TOKEN --------
        token = get_token(sec)

        # -------- CREATE PAYMENT LINK --------
        pay_url = create_payment_link(sec, token, order, user_phone)

        if pay_url:
            msg = f"✅ Order confirmed!\nPay here:\n{pay_url}"
        else:
            msg = "❌ Failed to create payment link"

        send_whatsapp(sec, user_phone, msg)

    except Exception as e:

        print("SYSTEM ERROR:", str(e))

    return {"statusCode": 200}
