import boto3
import json
import requests
import time
import uuid
from datetime import datetime, timedelta, timezone

# AWS Clients
state_table = boto3.resource('dynamodb').Table('FridgeState')
inventory_table = boto3.resource('dynamodb').Table('FridgeInventory')
sm = boto3.client('secretsmanager')

def get_secrets():
    res = sm.get_secret_value(SecretId='FridgeAgentSecrets')
    return json.loads(res['SecretString'])

def estimate_expiry(item_name):
    return (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

def get_token(sec):
    url = "https://pluraluat.v2.pinepg.in/api/auth/v1/token"
    payload = {
        "client_id": sec["PINE_CLIENT_ID"],
        "client_secret": sec["PINE_SECRET"],
        "grant_type": "client_credentials"
    }
    headers = {"accept": "application/json","content-type": "application/json"}
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    data = r.json()
    if "access_token" not in data:
        raise Exception(f"Token Error: {data}")
    return data["access_token"]

def create_payment_link(sec, token, user_phone, total_amount):
    order_id = f"FRID{int(time.time())}"
    expire_time = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%MZ")
    payload = {
        "merchant_id": str(sec["PINE_MERCH_ID"]),
        "order_id": order_id,
        "merchant_payment_link_reference": order_id,
        "description": "Restock items",
        "amount": {"value": int(total_amount*100), "currency": "INR"},
        "expire_by": expire_time,
        "allowed_payment_methods": ["UPI","CARD","NETBANKING"],
        "customer": {
            "mobile_number": str(user_phone)[-10:],
            "country_code": "91",
            "first_name": "Customer",
            "last_name": "Fridge",
            "email_id": "customer@example.com",
            "customer_id": str(user_phone)[-10:]
        },
        "product_details": [{
            "product_code": "Restock items",
            "product_amount": {"currency":"INR","value": int(total_amount*100)},
            "product_coupon_discount_amount": {"currency":"INR","value":0}
        }],
        "part_payment": False,
        "callback_url": "https://your-api-url/payment-success",
        "failure_callback_url": "https://your-api-url/payment-failure",
        "merchant_metadata": {"source": "fridge_agent"}
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Request-ID": str(uuid.uuid4()),
        "Request-Timestamp": datetime.now(timezone.utc).isoformat()
    }
    r = requests.post("https://pluraluat.v2.pinepg.in/api/pay/v1/paymentlink", headers=headers, json=payload, timeout=10)
    data = r.json()
    return data.get("payment_link")

def send_whatsapp(sec, user_phone, message):
    url = f"https://graph.facebook.com/v22.0/{sec['WHATSAPP_PHONE_ID']}/messages"
    headers = {"Authorization": f"Bearer {sec['WHATSAPP_TOKEN']}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp","to": user_phone,"text": {"body": message}}
    requests.post(url, headers=headers, json=payload)

def lambda_handler(event, context):
    sec = get_secrets()
    fridge_id = sec.get("FRIDGE_ID","fridge_001")

    # WhatsApp verification
    if event.get("httpMethod") == "GET":
        params = event.get("queryStringParameters", {})
        if params.get("hub.verify_token") == sec["VERIFY_TOKEN"]:
            return {"statusCode":200,"body":params.get("hub.challenge")}
        return {"statusCode":403}

    try:
        body = event.get("body", "{}")
        if isinstance(body,str): body = json.loads(body)
        value = body["entry"][0]["changes"][0]["value"]
        messages = value.get("messages", [])
        if not messages:
            return {"statusCode":200}

        # Only process first message
        message = messages[0]
        user_msg = message["text"]["body"].upper().strip()
        user_phone = message["from"]

        if "YES" not in user_msg:
            return {"statusCode":200}

        # Get pending order
        res = state_table.get_item(Key={"id":"pending_order"})
        order = res.get("Item")
        if not order:
            raise Exception("No pending order")

        # Skip if already processed
        if order.get("status") == "processed":
            return {"statusCode":200}

        # --- Calculate total order value ---
        total_amount = 0
        items_list = order.get("items")
        if items_list and isinstance(items_list, list):
            for item in items_list:
                total_amount += item.get("price",50)
                # Update inventory
                inventory_table.put_item(
                    Item={
                        "device_id": fridge_id,
                        "item_name": item.get("name","Unknown").lower(),
                        "last_updated": str(datetime.now()),
                        "quantity": item.get("quantity",1),
                        "price": item.get("price",50),
                        "expiry_date": estimate_expiry(item.get("name","Unknown")),
                        "status": "LOW"
                    }
                )
        else:
            # Single item fallback
            total_amount = order.get("price",50)
            inventory_table.put_item(
                Item={
                    "device_id": fridge_id,
                    "item_name": order.get("name","Milk").lower(),
                    "last_updated": str(datetime.now()),
                    "quantity": order.get("quantity",1),
                    "price": order.get("price",50),
                    "expiry_date": estimate_expiry(order.get("name","Milk")),
                    "status": "LOW"
                }
            )

        # Create single payment link
        token = get_token(sec)
        pay_url = create_payment_link(sec, token, user_phone, total_amount)

        if pay_url:
            send_whatsapp(sec, user_phone, f"✅ Order confirmed!\nPay here:\n{pay_url}")
        else:
            send_whatsapp(sec, user_phone, "❌ Failed to create payment link")

        # Mark order as processed
        state_table.update_item(
            Key={"id":"pending_order"},
            UpdateExpression="SET #st=:val",
            ExpressionAttributeNames={"#st":"status"},
            ExpressionAttributeValues={":val":"processed"}
        )

    except Exception as e:
        print("SYSTEM ERROR:", str(e))

    return {"statusCode":200}
