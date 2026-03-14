import boto3, base64, json, requests, os
import google.generativeai as genai
from datetime import datetime

# Initialize
s3 = boto3.client('s3')
db = boto3.resource('dynamodb').Table('FridgeState')
sm = boto3.client('secretsmanager')

def get_secrets():
    res = sm.get_secret_value(SecretId='FridgeAgentSecrets')
    print(res['SecretString'])
    return json.loads(res['SecretString'])


def lambda_handler(event, context):
    sec = get_secrets()
    
    # 1. Decode & Save Image to S3 (Bucket from Secrets)
    img_data = base64.b64decode(event['body'])
    img_key = f"fridge_{int(datetime.now().timestamp())}.jpg"
    s3.put_object(Bucket=sec["BUCKET_NAME"], Key=img_key, Body=img_data, ContentType='image/jpeg')

    # # 2. Get Weight from DynamoDB (set by IoT Rule)
    res = db.get_item(Key={'id': 'latest_weight'})
    weight = res.get('Item', {}).get('val', 0)


    # # 3. Gemini Vision Analysis
    genai.configure(api_key=sec['GEMINI_API_KEY'])
    print("Listing available models for this API Key:")
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f"AVAILABLE MODEL: {m.name}")
    except Exception as e:
        print(f"Could not list models: {e}")
    model = genai.GenerativeModel('models/gemini-2.5-flash')
    prompt = f"Weight is {weight}. If fridge items are low, list items and price. JSON format: {{'items': 'Milk', 'price': 50}}"
    ai_res = model.generate_content([prompt, {'mime_type': 'image/jpeg', 'data': img_data}])
    # analysis = json.loads(ai_res.text.replace("```json", "").replace("```", ""))
    # print(analysis)

    # # 4. Save Pending Order & Send WhatsApp
    db.put_item(Item={'id': 'pending_order', 'price': 100, 'name': 'garlic'})
    
    wa_url = f"https://graph.facebook.com/v18.0/{sec['WHATSAPP_PHONE_ID']}/messages"
    headers = {"Authorization": f"Bearer {sec['WHATSAPP_TOKEN']}", "Content-Type": "application/json"}
    # msg = f"🚨 Fridge Alert!\nLow on {analysis['items']}.\nCost: {analysis['price']} INR.\nReply 'YES' to order."
    msg = f"🚨 Fridge Alert!\nLow on {"Milk"}.\nCost: {"100"} INR.\nReply 'YES' to order."
    
    payload = {
        "messaging_product": "whatsapp", 
        "to": sec['MY_PHONE_NUMBER'], 
        "type": "text", 
        "text": {"body": msg}
    }

    print(f"DEBUG: Sending WhatsApp to {sec['MY_PHONE_NUMBER']} via ID {sec['WHATSAPP_PHONE_ID']}")
    
    try:
        print("DEBUG: Attempting network call to Meta...")
        # Added 'timeout=10' to prevent the code from just 'breaking' silently
        wa_res = requests.post(wa_url, headers=headers, json=payload, timeout=10)
        
        print(f"DEBUG: Response Code: {wa_res.status_code}")
        print(f"DEBUG: Response Body: {wa_res.text}")
        
    except requests.exceptions.Timeout:
        print("ERROR: Meta API timed out. Is the Lambda timeout high enough?")
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Requests library failed: {str(e)}")
    except Exception as e:
        print(f"ERROR: An unexpected error occurred: {str(e)}")
    return {'statusCode': 200, 'body': 'OK'}
