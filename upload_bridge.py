import boto3
import json
import requests
import google.generativeai as genai
from datetime import datetime, timedelta

# -------- AWS CLIENTS --------
s3 = boto3.client('s3')
state_table = boto3.resource('dynamodb').Table('FridgeState')
inventory_table = boto3.resource('dynamodb').Table('FridgeInventory')
sm = boto3.client('secretsmanager')


# -------- GET SECRETS --------
def get_secrets():
    res = sm.get_secret_value(SecretId='FridgeAgentSecrets')
    return json.loads(res['SecretString'])

# -------- ESTIMATE EXPIRY FALLBACK --------
def estimate_expiry(item_name):
    item_name_lower = item_name.lower()
    if "milk" in item_name_lower:
        return (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    if "egg" in item_name_lower:
        return (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    if "bread" in item_name_lower:
        return (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    # Default fallback
    return (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

# -------- MAIN LAMBDA --------
def lambda_handler(event, context):
    sec = get_secrets()

    try:
        # -------- 1 FETCH IMAGE FROM S3 --------
        test_bucket = "items-grocery"
        test_key = "20260314_152222.jpg.jpeg"

        s3_obj = s3.get_object(Bucket=test_bucket, Key=test_key)
        img_data = s3_obj['Body'].read()

        # -------- 2 GET WEIGHT --------
        res = state_table.get_item(Key={'id': 'latest_weight'})
        weight = res.get('Item', {}).get('val', 0)

        # -------- 3 GEMINI VISION ANALYSIS --------
        genai.configure(api_key=sec['GEMINI_API_KEY'])
        model = genai.GenerativeModel('models/gemini-2.5-flash')

        prompt = f"""
        Analyze this fridge image along with the weight sensor reading of {weight} grams.
        Detect all grocery items that are low or missing.

        For each item, return ONLY valid JSON in this structure:
        {{
            "items": [
                {{
                    "name": "Milk",
                    "quantity": 1,
                    "price": 50,
                    "expiry_date": "2026-03-21"
                }}
            ]
        }}

        If expiry_date is visible on the packaging, use it. Otherwise leave it empty and we'll estimate.
        Prices should be realistic Indian grocery prices.
        """

        ai_res = model.generate_content([
            prompt,
            {'mime_type': 'image/jpeg', 'data': img_data}
        ])
        print("Gemini raw response:", ai_res.text)

        # -------- 4 PARSE JSON SAFELY --------
        try:
            raw = ai_res.text.replace("```json", "").replace("```", "").strip()
            analysis = json.loads(raw)
        except Exception as e:
            print("Gemini parse failed:", str(e))
            analysis = {"items": []}

        # -------- 5 ENSURE ITEMS STRUCTURE & FILL DEFAULTS --------
        for item in analysis.get("items", []):
            if "name" not in item:
                item["name"] = "Unknown"
            if "quantity" not in item:
                item["quantity"] = 1
            if "price" not in item:
                item["price"] = 50
            # Use Gemini expiry if detected, else estimate
            if "expiry_date" not in item or not item["expiry_date"]:
                item["expiry_date"] = estimate_expiry(item["name"])

        # -------- 6 ENSURE MILK EXISTS --------
        has_milk = any(i["name"].lower() == "milk" for i in analysis["items"])
        if not has_milk:
            milk_item = {"name": "Milk", "quantity": 1, "price": 50, "expiry_date": estimate_expiry("Milk")}
            analysis["items"].append(milk_item)

        # -------- 7 CALCULATE TOTAL PRICE --------
        total_price = sum(i["price"] for i in analysis["items"])
        analysis["total_estimated_price"] = total_price

        # -------- 8 FORMAT ITEM SUMMARY --------
        items_summary_list = [
            f"{i['name']} ({i['quantity']}) - ₹{i['price']}" for i in analysis["items"]
        ]
        items_str = ", ".join(items_summary_list)

        # -------- 9 SAVE ORDER TO STATE TABLE --------
        order_item = {
            'id': 'pending_order',
            'timestamp': str(datetime.now()),
            'price': total_price,
            'items_json': json.dumps(analysis["items"]),
            'summary': items_str,
            'status': 'PENDING'
        }
        state_table.put_item(Item=order_item)

        # -------- 10 UPDATE FRIDGE INVENTORY --------
        for item in analysis["items"]:
            inventory_table.put_item(
                Item={
                    "device_id": "fridge_001", 
                    "item_name": item["name"].lower(),
                    "last_updated": str(datetime.now()),
                    "quantity": item["quantity"],
                    "price": item["price"],
                    "expiry_date": item["expiry_date"],
                    "status": "LOW"
                }
            )

        # -------- 11 BUILD WHATSAPP MESSAGE --------
        item_details_msg = "\n".join([
            f"- {i['name']} ({i['quantity']}) - ₹{i['price']} (Expiry: {i['expiry_date']})"
            for i in analysis["items"]
        ])

        msg = f"""
🚨 *Fridge Alert!*

*Low Items:*
{item_details_msg}

*Total Cost:* ₹{total_price}

Reply *YES* to order.
"""

        # -------- 12 SEND WHATSAPP --------
        wa_url = f"https://graph.facebook.com/v18.0/{sec['WHATSAPP_PHONE_ID']}/messages"
        headers = {
            "Authorization": f"Bearer {sec['WHATSAPP_TOKEN']}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": sec["MY_PHONE_NUMBER"],
            "type": "text",
            "text": {"body": msg}
        }
        wa_res = requests.post(wa_url, headers=headers, json=payload, timeout=10)
        print("WhatsApp response:", wa_res.status_code, wa_res.text)

        return {
            'statusCode': 200,
            'body': json.dumps({"order": items_str, "total": total_price})
        }

    except Exception as e:
        print("SYSTEM ERROR:", str(e))
        return {
            'statusCode': 500,
            'body': json.dumps({"error": str(e)})
        }
