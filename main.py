import sys
sys.path.insert(0, './lib')
import os
import json
import asyncio
import configparser
import websockets
import requests
import pandas as pd

def headers_to_dict(response):
    """
    Transforme les en-têtes de réponse HTTP en dictionnaire structuré.

    :param response: Objet de réponse HTTP.
    :return: Dictionnaire contenant les en-têtes structurés.
    """
    extracted_headers = {}
    for header, header_value in response.headers.items():
        parsed_dict = {}
        entries = header_value.split(', ')
        for entry in entries:
            key_value = entry.split(';')[0]
            if '=' in key_value:
                key, value = key_value.split('=', 1)
                parsed_dict[key.strip()] = value.strip()
        extracted_headers[header] = parsed_dict if parsed_dict else header_value
    return extracted_headers

def flatten_and_clean_json(all_data, sep='.'):
    """
    Aplatit des données JSON imbriquées et réorganise les colonnes en fonction des objets les plus complets.

    :param all_data: Liste de dictionnaires JSON à aplatir.
    :param sep: Séparateur utilisé pour les clés aplaties.
    :return: Liste de dictionnaires aplatis et nettoyés.
    """
    all_keys = set()
    flattened_data = []

    def flatten(nested_json, parent_key=''):
        """Fonction interne pour aplatir un JSON imbriqué."""
        flat_dict = {}
        for key, value in nested_json.items():
            new_key = f'{parent_key}{sep}{key}' if parent_key else key
            if isinstance(value, dict):
                flat_dict.update(flatten(value, new_key))
            else:
                flat_dict[new_key] = value
            all_keys.add(new_key)
        return flat_dict

    for item in all_data:
        flat_item = flatten(item)
        flattened_data.append(flat_item)

    max_properties_object = max(flattened_data, key=len)
    column_order = list(max_properties_object.keys())

    cleaned_data = []
    for item in flattened_data:
        cleaned_item = {key: item[key] for key in column_order if key in item and item[key] is not None}
        cleaned_data.append(cleaned_item)

    return cleaned_data

def transform_data_types(df):
    """
    Transforme les types de données d'un DataFrame Pandas :
    - Convertit les colonnes de type timestamp en format date français.
    - Formate les montants en valeurs numériques avec séparateur français.

    :param df: DataFrame contenant les données.
    :return: DataFrame transformé.
    """
    timestamp_columns = ['timestamp']  # Colonnes de type timestamp
    for col in timestamp_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%d/%m/%Y')

    amount_columns = ['amount.value', 'amount.fractionDigits', 'subAmount.value', 'subAmount.fractionDigits']
    for col in amount_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            df[col] = df[col].apply(lambda x: str(x).replace('.', ',') if pd.notna(x) else x)
    
    return df

async def fetch_all_transactions(token):
    """
    Fonction principale :
    Récupère toutes les transactions via WebSocket et les sauvegarde dans un fichier.

    :param token: Token de session pour authentification.
    """
    all_data = []
    message_id = 1

    async with websockets.connect("wss://api.traderepublic.com") as websocket:
        locale_config = {"locale": "fr"}
        await websocket.send(f"connect 31 {json.dumps(locale_config)}")
        await websocket.recv()  # Réponse de connexion

        print("✅ Connexion à la WebSocket réussie!\n⏳ Veuillez patienter...")
        
        after_cursor = None
        while True:
            payload = {"type": "timelineTransactions", "token": token}
            if after_cursor:
                payload["after"] = after_cursor
            
            await websocket.send(f"sub {message_id} {json.dumps(payload)}")
            message_id += 1
            
            response = await websocket.recv()
            start_index = response.find('{')
            end_index = response.rfind('}')
            response = response[start_index:end_index + 1] if start_index != -1 and end_index != -1 else "{}"
            data = json.loads(response)
            
            if not data.get("items"):
                break

            all_data.extend(data["items"])
            after_cursor = data.get("cursors", {}).get("after")
            await websocket.send(f"unsub {message_id}")
            await websocket.recv()
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
            df = transform_data_types(df)
            output_path = os.path.join(output_folder, "trade_republic_transactions.csv")
            df.to_csv(output_path, index=False, sep=";", encoding="utf-8-sig")
            print("✅ Données sauvegardées dans 'trade_republic_transactions.csv'")

if __name__ == "__main__":
    # Chargement de la configuration
    config = configparser.ConfigParser()
    config.read('config.ini')

    # Variables de configuration
    phone_number = config['secret']['phone_number']
    pin = config['secret']['pin']
    output_format = config['general']['output_format']  # Format de sortie : json ou csv
    output_folder = config['general']['output_folder']
    os.makedirs(output_folder, exist_ok=True)

    # Validation du format de sortie
    if output_format.lower() not in ["json", "csv"]:
        print(f"❌ Le format '{output_format}' est inconnu. Veuillez saisir 'json' ou 'csv'.")
        exit()

    # Envoi de la requête de connexion
    response = requests.post(
        "https://api.traderepublic.com/api/v1/auth/web/login",
        json={"phoneNumber": phone_number, "pin": pin}
    ).json()

    # Récupération des informations de connexion
    process_id = response.get('processId')
    countdown = response.get('countdownInSeconds')
    if not process_id:
        print("❌ Échec de l'initialisation de la connexion. Vérifiez votre numéro de téléphone et PIN.")
        exit()

    # Saisie du code 2FA
    code = input(f"❓ Entrez le code 2FA reçu ({countdown} secondes restantes) ou tapez 'SMS': ")

    # Si l'utilisateur choisit de recevoir le code 2FA par SMS, une requête est envoyée pour renvoyer le code.
    if code == 'SMS':
        requests.post(f"https://api.traderepublic.com/api/v1/auth/web/login/{process_id}/resend")
        code = input("❓ Entrez le code 2FA reçu par SMS: ")

    # Vérification de l'appareil avec le code 2FA saisi.
    response = requests.post(f"https://api.traderepublic.com/api/v1/auth/web/login/{process_id}/{code}")
    if response.status_code != 200:
        print("❌ Échec de la vérification de l'appareil. Vérifiez le code et réessayez.")
        exit()

    print("✅ Appareil vérifié avec succès!")

    # Extraction du token de session
    response_headers = headers_to_dict(response)
    session_token = response_headers.get('Set-Cookie', {}).get('tr_session')
    if not session_token:
        print("❌ Token de connexion introuvable.")
        exit()

    print("✅ Token de connexion trouvé!")

    # Exécution de la récupération des transactions
    asyncio.run(fetch_all_transactions(session_token))