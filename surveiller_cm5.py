"""Surveille la disponibilité du Raspberry Pi Compute Module 5
(4 Go RAM / 32 Go eMMC, CM5004032 / SC1565) chez PiShop.ca et DigiKey.ca.

Conçu pour GitHub Actions : une vérification par exécution. L'état précédent
est lu et écrit dans etat.json pour ne notifier qu'au passage rupture -> en stock.

Variables d'environnement (secrets du dépôt) :
  NTFY_TOPIC              sujet ntfy.sh pour la notification mobile (obligatoire)
  DIGIKEY_CLIENT_ID       identifiants de l'API DigiKey (facultatifs ;
  DIGIKEY_CLIENT_SECRET   sans eux, DigiKey est ignoré)
  TEST_NOTIFICATION=1     envoie seulement une notification de test

Bibliothèque standard uniquement.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL_PISHOP = "https://www.pishop.ca/product/raspberry-pi-compute-module-5-4gb-ram-32gb-emmc-cm5004032/"
URL_DIGIKEY = "https://www.digikey.ca/fr/products/detail/raspberry-pi/SC1565/25805586"
PIECE_DIGIKEY = "SC1565"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Surveiller-CM5/1.0"
FICHIER_ETAT = Path(__file__).with_name("etat.json")


def journal(message):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC  {message}", flush=True)


def requete(url, data=None, entetes=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(entetes or {})})
    with urllib.request.urlopen(req, timeout=30) as rep:
        return rep.read().decode("utf-8", errors="replace")


def envoyer_mobile(titre, texte, url):
    sujet = os.environ.get("NTFY_TOPIC")
    if not sujet:
        journal("Mobile  : NTFY_TOPIC absent, notification non envoyée")
        return
    corps = {
        "topic": sujet,
        "title": titre,
        "message": texte,
        "click": url,
        "priority": 5,
        "tags": ["rotating_light"],
        "actions": [{"action": "view", "label": "Ouvrir la page", "url": url}],
    }
    requete("https://ntfy.sh/", json.dumps(corps).encode("utf-8"), {"Content-Type": "application/json"})
    journal(f"Mobile  : notification envoyée ({titre})")


def tester_pishop():
    """Retourne True (en stock), False (rupture) ou None (indéterminé)."""
    html = requete(URL_PISHOP)
    if re.search(r"schema\.org/InStock", html):
        return True
    if re.search(r"schema\.org/OutOfStock", html):
        return False
    return None


def tester_digikey(client_id, client_secret):
    """Retourne la quantité disponible chez DigiKey Canada (API Product Information v4)."""
    jeton = json.loads(requete(
        "https://api.digikey.com/v1/oauth2/token",
        urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"},
    ))["access_token"]
    rep = json.loads(requete(
        f"https://api.digikey.com/products/v4/search/{PIECE_DIGIKEY}/productdetails",
        entetes={
            "Authorization": f"Bearer {jeton}",
            "X-DIGIKEY-Client-Id": client_id,
            "X-DIGIKEY-Locale-Site": "CA",
            "X-DIGIKEY-Locale-Language": "fr",
            "X-DIGIKEY-Locale-Currency": "CAD",
        },
    ))
    produit = rep.get("Product") or {}
    journal(f"DigiKey : produit trouvé {produit.get('ManufacturerProductNumber', '?')} - "
            f"{(produit.get('Description') or {}).get('ProductDescription', '?')}")
    # L'API omet QuantityAvailable quand il vaut 0 ; repli sur la somme par type d'emballage.
    if "QuantityAvailable" in produit:
        return int(produit["QuantityAvailable"])
    variantes = produit.get("ProductVariations") or []
    return sum(int(v.get("QuantityAvailableforPackageType") or 0) for v in variantes)


def main():
    if os.environ.get("TEST_NOTIFICATION") == "1":
        envoyer_mobile("Test : surveillant CM5", "Ceci est une notification de test.", URL_PISHOP)
        return 0

    etat = json.loads(FICHIER_ETAT.read_text()) if FICHIER_ETAT.exists() else {}
    nouvel_etat = dict(etat)
    erreurs = 0

    try:
        dispo = tester_pishop()
        if dispo is None:
            journal("PiShop  : état indéterminé (page modifiée ?)")
            erreurs += 1
        else:
            journal(f"PiShop  : {'EN STOCK' if dispo else 'rupture'}")
            if dispo and not etat.get("pishop"):
                envoyer_mobile("CM5 disponible chez PiShop.ca",
                               "Le Compute Module 5 (4 Go / 32 Go eMMC) est en stock.", URL_PISHOP)
            nouvel_etat["pishop"] = dispo
    except Exception as e:
        journal(f"PiShop  : erreur - {e}")
        erreurs += 1

    client_id = os.environ.get("DIGIKEY_CLIENT_ID")
    client_secret = os.environ.get("DIGIKEY_CLIENT_SECRET")
    if client_id and client_secret:
        try:
            qte = tester_digikey(client_id, client_secret)
            journal(f"DigiKey : {'EN STOCK' if qte > 0 else 'rupture'} (quantité : {qte})")
            if qte > 0 and not etat.get("digikey"):
                envoyer_mobile("CM5 disponible chez DigiKey.ca", f"Quantité disponible : {qte}", URL_DIGIKEY)
            nouvel_etat["digikey"] = qte > 0
        except Exception as e:
            journal(f"DigiKey : erreur - {e}")
            erreurs += 1
    else:
        journal("DigiKey : ignoré (pas d'identifiants)")

    if nouvel_etat != etat:
        FICHIER_ETAT.write_text(json.dumps(nouvel_etat, indent=2) + "\n")
        journal("État mis à jour")

    # Une erreur isolée ne fait pas échouer l'exécution : GitHub enverrait un courriel à chaque fois.
    return 0


if __name__ == "__main__":
    sys.exit(main())
