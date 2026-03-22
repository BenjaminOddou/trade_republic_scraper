import os
import json
import asyncio
import configparser
import websockets
import requests
import pandas as pd
import hashlib
import uuid
import base64
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def headers_to_dict(response):
    """
    Transforme les en-têtes de réponse HTTP en dictionnaire structuré.

    :param response: Objet de réponse HTTP.
    :return: Dictionnaire contenant les en-têtes structurés.
    """
    extracted_headers = {}
    for header, header_value in response.headers.items():
        parsed_dict = {}
        entries = header_value.split(", ")
        for entry in entries:
            key_value = entry.split(";")[0]
            if "=" in key_value:
                key, value = key_value.split("=", 1)
                parsed_dict[key.strip()] = value.strip()
        extracted_headers[header] = parsed_dict if parsed_dict else header_value
    return extracted_headers

def flatten_and_clean_json(all_data, sep="."):
    """
    Aplatit des données JSON imbriquées et préserve l'ordre des colonnes.

    :param all_data: Liste de dictionnaires JSON à aplatir.
    :param sep: Séparateur utilisé pour les clés aplaties.
    :return: Liste de dictionnaires aplatis et nettoyés.
    """
    all_keys = []  # Utilisé pour conserver l'ordre des colonnes
    flattened_data = []

    def flatten(nested_json, parent_key=""):
        """Aplatit récursivement un JSON imbriqué."""
        flat_dict = {}
        for key, value in nested_json.items():
            new_key = f"{parent_key}{sep}{key}" if parent_key else key
            if isinstance(value, dict):
                flat_dict.update(flatten(value, new_key))
            else:
                flat_dict[new_key] = value

            if new_key not in all_keys:
                all_keys.append(new_key)

        return flat_dict

    for item in all_data:
        flat_item = flatten(item)
        flattened_data.append(flat_item)

    complete_data = [
        {key: item.get(key, None) for key in all_keys} for item in flattened_data
    ]
    return complete_data

def transform_data_types(df):
    """
    Transforme les types de données d'un DataFrame Pandas :
    - Convertit les colonnes de type timestamp en format date français.
    - Formate les montants en valeurs numériques avec séparateur français.

    :param df: DataFrame contenant les données.
    :return: DataFrame transformé.
    """
    timestamp_columns = ["timestamp"]
    for col in timestamp_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%d/%m/%Y")

    amount_columns = [
        "amount.value",
        "amount.fractionDigits",
        "subAmount.value",
        "subAmount.fractionDigits",
    ]
    for col in amount_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].apply(
                lambda x: str(x).replace(".", ",") if pd.notna(x) else x
            )
    return df

def generate_device_info():
    """Génère dynamiquement un Device Info cohérent au format Base64"""
    device_id = hashlib.sha512(uuid.uuid4().bytes).hexdigest()
    device_info = {
        "stableDeviceId": device_id,
    }
    return base64.b64encode(json.dumps(device_info).encode()).decode()

def get_waf_token_with_selenium():
    """Utilise Selenium en mode Headless pour obtenir le token AWS WAF silencieusement."""
    print("🤖 Veuillez patienter, Selenium gère le navigateur en arrière-plan...")
    
    options = Options()
    options.add_argument("--headless=new")
    
    try:
        # Selenium 4 gère automatiquement le téléchargement du driver et du navigateur ici
        driver = webdriver.Chrome(options=options)
        
        # Masquer encore plus le fait que ce soit un robot via CDP
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            """
        })
        
        driver.get("https://app.traderepublic.com/")
        # 5 secondes pour laisser le temps au WAF de se déclencher et de déposer le cookie
        time.sleep(5)
        
        waf_token = None
        
        # Récupération via les cookies (Méthode principale)
        for cookie in driver.get_cookies():
            if "aws-waf-token" in cookie.get("name", ""):
                waf_token = cookie["value"]
                break
                
        # Récupération via l'API Javascript (Fallback si le cookie est caché)
        if not waf_token:
            try:
                waf_token = driver.execute_script("return window.AWSWafIntegration && window.AWSWafIntegration.getToken();")
            except:
                pass
                
        driver.quit()
        
        if waf_token:
            print("✅ Token de sécurité WAF récupéré avec succès !")
            return waf_token
        else:
            print("⚠️ Impossible d'obtenir le token de sécurité automatiquement.")
            return ""
            
    except Exception as e:
        print(f"❌ Erreur lors de l'automatisation Selenium : {e}")
        return ""

async def connect_to_websocket():
    """
    Fonction asynchrone pour établir une connexion WebSocket à l'API de TradeRepublic.

    :return: L'objet WebSocket connecté à l'API de TradeRepublic.
    """
    websocket = await websockets.connect("wss://api.traderepublic.com")
    locale_config = {
        "locale": "fr",
        "platformId": "webtrading",
        "platformVersion": "safari - 18.3.0",
        "clientId": "app.traderepublic.com",
        "clientVersion": "3.151.3",
    }
    await websocket.send(f"connect 31 {json.dumps(locale_config)}")
    await websocket.recv()

    print("✅ Connexion à la WebSocket réussie!\n⏳ Récupération des données...")
    return websocket

async def fetch_transaction_details(websocket, transaction_id, token, message_id):
    """
    Récupère les détails d'une transaction spécifique via WebSocket.

    Cette fonction envoie une requête WebSocket pour récupérer les informations détaillées d'une transaction
    spécifique en utilisant son `transaction_id`. Elle récupère ensuite une réponse et extrait les informations
    demandées, notamment les éléments de la section "Transaction". Si une erreur ou un délai se produit, un message
    d'avertissement est imprimé. La fonction retourne un dictionnaire contenant les informations extraites de la transaction.

    :param websocket: L'objet WebSocket déjà connecté à l'API de TradeRepublic.
    :param transaction_id: L'identifiant unique de la transaction pour laquelle les détails doivent être récupérés.
    :param token: Le token de session utilisé pour l'authentification.
    :param message_id: L'identifiant du message qui est incrémenté à chaque requête pour éviter les conflits dans les abonnements.

    :return: Un tuple contenant deux éléments :
        - `transaction_data`: Un dictionnaire avec les informations extraites de la transaction.
        - `message_id`: L'ID du message incrémenté après chaque requête pour gérer l'abonnement/désabonnement.
    """
    payload = {"type": "timelineDetailV2", "id": transaction_id, "token": token}
    message_id += 1
    await websocket.send(f"sub {message_id} {json.dumps(payload)}")
    response = await websocket.recv()
    await websocket.send(f"unsub {message_id}")
    await websocket.recv()

    start_index = response.find("{")
    end_index = response.rfind("}")
    response_data = json.loads(
        response[start_index : end_index + 1]
        if start_index != -1 and end_index != -1
        else "{}"
    )

    transaction_data = {}

    for section in response_data.get("sections", []):
        if section.get("title") == "Transaction":
            for item in section.get("data", []):
                header = item.get("title")
                value = item.get("detail", {}).get("text")
                if header and value:
                    transaction_data[header] = value

    return transaction_data, message_id

async def fetch_all_transactions(token, extract_details):
    """
    Fonction principale qui récupère toutes les transactions via WebSocket et les sauvegarde dans un fichier.

    Cette fonction se connecte à l'API WebSocket de TradeRepublic pour récupérer les informations
    relatives aux transactions de l'utilisateur, soit sous forme de JSON, soit sous forme de CSV.
    Si l'option `details` est activée, elle récupère les détails des transactions supplémentaires.

    Le processus implique l'abonnement à un flux de transactions, la gestion de la pagination,
    la collecte des données et leur sauvegarde dans un fichier à la fin.

    :param token: Token de session pour l'authentification. Il est nécessaire pour valider les requêtes de l'API.
    :param details: Booléen déterminant si des détails supplémentaires sur chaque transaction doivent être récupérés.
                    Si `True`, chaque transaction sera enrichie de données supplémentaires ; sinon, seules les transactions de base seront récupérées.
    :return: Elle sauvegarde les données récupérées dans un fichier (soit JSON, soit CSV) dans le dossier spécifié.
    """
    all_data = []
    message_id = 0

    async with await connect_to_websocket() as websocket:
        after_cursor = None
        while True:
            payload = {"type": "timelineTransactions", "token": token}
            if after_cursor:
                payload["after"] = after_cursor

            message_id += 1
            await websocket.send(f"sub {message_id} {json.dumps(payload)}")
            response = await websocket.recv()
            await websocket.send(f"unsub {message_id}")
            await websocket.recv()
            start_index = response.find("{")
            end_index = response.rfind("}")
            response = (
                response[start_index : end_index + 1]
                if start_index != -1 and end_index != -1
                else "{}"
            )
            data = json.loads(response)

            if not data.get("items"):
                break

            if extract_details:
                for transaction in data["items"]:
                    transaction_id = transaction.get("id")
                    if transaction_id:
                        details, message_id = await fetch_transaction_details(
                            websocket, transaction_id, token, message_id
                        )
                        transaction.update(details)
                    all_data.append(transaction)
            else:
                all_data.extend(data["items"])

            after_cursor = data.get("cursors", {}).get("after")
            if not after_cursor:
                break

    if output_format.lower() == "json":
        output_path = os.path.join(output_folder, "trade_republic_transactions.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_data, f, indent=4, ensure_ascii=False)
        print("✅ Données sauvegardées dans 'trade_republic_transactions.json'")
    else:
        flattened_data = flatten_and_clean_json(all_data)
        if flattened_data:
            df = pd.DataFrame(flattened_data)
            df = df.dropna(axis=1, how="all")
            df = transform_data_types(df)
            output_path = os.path.join(output_folder, "trade_republic_transactions.csv")
            df.to_csv(output_path, index=False, sep=";", encoding="utf-8-sig")
            print("✅ Données sauvegardées dans 'trade_republic_transactions.csv'")

async def profile_cash(token):
    """
    Récupère les informations de profil de l'utilisateur via WebSocket.

    :param token: Le token de session utilisé pour l'authentification.
    :return: Un dictionnaire contenant les informations du profil utilisateur.
    """
    async with await connect_to_websocket() as websocket:
        payload = {"type": "availableCash", "token": token}
        await websocket.send(f"sub 1 {json.dumps(payload)}")
        response = await websocket.recv()

        start_index = response.find("[")
        end_index = response.rfind("]")
        response_data = json.loads(
            response[start_index : end_index + 1]
            if start_index != -1 and end_index != -1
            else "[]"
        )

        if output_format.lower() == "json":
            output_path = os.path.join(
                output_folder, "trade_republic_profile_cash.json"
            )
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(response_data, f, indent=4, ensure_ascii=False)
            print("✅ Données sauvegardées dans 'trade_republic_profile_cash.json'")
        else:
            flattened_data = flatten_and_clean_json(response_data)
            if flattened_data:
                df = pd.DataFrame(flattened_data)
                output_path = os.path.join(
                    output_folder, "trade_republic_profile_cash.csv"
                )
                df.to_csv(output_path, index=False, sep=";", encoding="utf-8-sig")
                print("✅ Données sauvegardées dans 'trade_republic_profile_cash.csv'")

if __name__ == "__main__":
    # Chargement de la configuration
    config = configparser.ConfigParser()
    config.read("config.ini")

    # Variables de configuration
    try:
        phone_number = config.get("secret", "phone_number")
        pin = config.get("secret", "pin")
    except (configparser.NoSectionError, configparser.NoOptionError):
        print("❌ Erreur : Veuillez vérifier que 'phone_number' et 'pin' sont bien renseignés dans config.ini")
        exit()
    
    # Paramètres WAF et Device (optionnels dans le config.ini, générés automatiquement sinon)
    waf_token = config.get("secret", "waf_token", fallback="")
    device_info = config.get("secret", "device_info", fallback="")
    
    output_format = config.get("general", "output_format", fallback="csv")
    output_folder = config.get("general", "output_folder", fallback="out")
    extract_details = config.getboolean("general", "extract_details", fallback=False)
    os.makedirs(output_folder, exist_ok=True)

    if output_format.lower() not in ["json", "csv"]:
        print(f"❌ Le format '{output_format}' est inconnu. Veuillez saisir 'json' ou 'csv'.")
        exit()

    # Si les tokens ne sont pas écrits en dur dans config.ini, on les génère automatiquement
    if not device_info:
        device_info = generate_device_info()
    
    if not waf_token:
        waf_token = get_waf_token_with_selenium()

    headers = {
        "Accept": "*/*",
        "Accept-Language": "fr",
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        "Pragma": "no-cache",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "x-aws-waf-token": waf_token,
        "x-tr-app-version": "13.40.5",
        "x-tr-device-info": device_info,
        "x-tr-platform": "web"
    }

    print("📡 Tentative de connexion à l'API Trade Republic...")
    login_response = requests.post(
        "https://api.traderepublic.com/api/v1/auth/web/login",
        json={"phoneNumber": phone_number, "pin": pin},
        headers=headers
    )

    if login_response.status_code != 200:
        print(f"❌ Erreur lors de la connexion à l'API Trade Republic (Code HTTP {login_response.status_code}).")
        print("Le pare-feu Amazon (WAF) a probablement bloqué la requête ou le jeton a expiré.")
        print(f"Détails bruts : {login_response.text}")
        exit()

    try:
        login_data = login_response.json()
    except ValueError:
        print("❌ L'API n'a pas renvoyé de JSON valide.")
        exit()

    process_id = login_data.get("processId")
    countdown = login_data.get("countdownInSeconds")
    
    if not process_id:
        print("❌ Échec de l'initialisation de la connexion. Vérifiez vos identifiants.")
        exit()

    code = input(f"❓ Entrez le code 2FA reçu ({countdown} secondes restantes) ou tapez 'SMS': ")

    if code == "SMS":
        requests.post(
            f"https://api.traderepublic.com/api/v1/auth/web/login/{process_id}/resend",
            headers=headers
        )
        code = input("❓ Entrez le code 2FA reçu par SMS: ")

    verify_response = requests.post(
        f"https://api.traderepublic.com/api/v1/auth/web/login/{process_id}/{code}",
        headers=headers
    )
    
    if verify_response.status_code != 200:
        print(f"❌ Échec de la vérification de l'appareil (Code HTTP {verify_response.status_code}).")
        print(f"Détails bruts : {verify_response.text}")
        exit()

    print("✅ Appareil vérifié avec succès!")

    response_headers = headers_to_dict(verify_response)
    session_token = response_headers.get("Set-Cookie", {}).get("tr_session")
    
    if not session_token:
        print("❌ Token de connexion introuvable. Trade Republic a peut-être changé sa méthode d'authentification.")
        exit()

    print("✅ Token de session récupéré avec succès!")

    asyncio.run(fetch_all_transactions(session_token, extract_details))
    asyncio.run(profile_cash(session_token))