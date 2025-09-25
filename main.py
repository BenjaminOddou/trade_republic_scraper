import sys

sys.path.insert(0, "./lib")

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

    # Aplatir toutes les entrées et collecter toutes les colonnes possibles
    for item in all_data:
        flat_item = flatten(item)
        flattened_data.append(flat_item)

    # Assurer que chaque dictionnaire a toutes les colonnes, avec ordre inchangé
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
    timestamp_columns = ["timestamp"]  # Colonnes de type timestamp
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
    await websocket.recv()  # Réponse de connexion

    print("✅ Connexion à la WebSocket réussie!\n⏳ Veuillez patienter...")
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
    phone_number = config.get("secret", "phone_number")
    pin = config.get("secret", "pin")
    output_format = config.get(
        "general", "output_format"
    )  # Format de sortie : json ou csv
    output_folder = config.get("general", "output_folder")
    extract_details = config.getboolean("general", "extract_details", fallback=False)
    os.makedirs(output_folder, exist_ok=True)

    # Validation du format de sortie
    if output_format.lower() not in ["json", "csv"]:
        print(
            f"❌ Le format '{output_format}' est inconnu. Veuillez saisir 'json' ou 'csv'."
        )
        exit()

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    }

    response = requests.post(
        "https://api.traderepublic.com/api/v1/auth/web/login",
        json={"phoneNumber": phone_number, "pin": pin},
        headers=headers
    ).json()

    # Récupération des informations de connexion
    process_id = response.get("processId")
    countdown = response.get("countdownInSeconds")
    if not process_id:
        print(
            "❌ Échec de l'initialisation de la connexion. Vérifiez votre numéro de téléphone et PIN."
        )
        exit()

    # Saisie du code 2FA
    code = input(
        f"❓ Entrez le code 2FA reçu ({countdown} secondes restantes) ou tapez 'SMS': "
    )

    # Si l'utilisateur choisit de recevoir le code 2FA par SMS, une requête est envoyée pour renvoyer le code.
    if code == "SMS":
        requests.post(
            f"https://api.traderepublic.com/api/v1/auth/web/login/{process_id}/resend",
            headers=headers
        )
        code = input("❓ Entrez le code 2FA reçu par SMS: ")

    # Vérification de l'appareil avec le code 2FA saisi.
    response = requests.post(
        f"https://api.traderepublic.com/api/v1/auth/web/login/{process_id}/{code}",
        headers=headers
    )
    if response.status_code != 200:
        print(
            "❌ Échec de la vérification de l'appareil. Vérifiez le code et réessayez."
        )
        exit()

    print("✅ Appareil vérifié avec succès!")

    # Extraction du token de session
    response_headers = headers_to_dict(response)
    session_token = response_headers.get("Set-Cookie", {}).get("tr_session")
    if not session_token:
        print("❌ Token de connexion introuvable.")
        exit()

    print("✅ Token de connexion trouvé!")

    # Exécution de la récupération des transactions
    asyncio.run(fetch_all_transactions(session_token, extract_details))
    # Exécution de la récupération des informations de profil
    asyncio.run(profile_cash(session_token))