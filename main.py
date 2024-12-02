import logging
import os
import time
import requests
from openai import OpenAI
from plexapi.server import PlexServer
from plexapi.exceptions import NotFound
from utils.classes import UserInputs

userInputs = UserInputs(
    plex_url=os.getenv("PLEX_URL"),
    plex_token=os.getenv("PLEX_TOKEN"),
    openai_key=os.getenv("OPEN_AI_KEY"),
    library_names=os.getenv("LIBRARY_NAMES").split(","),
    collection_title=os.getenv("COLLECTION_TITLE"),
    history_amount=int(os.getenv("HISTORY_AMOUNT")),
    recommended_amount=int(os.getenv("RECOMMENDED_AMOUNT")),
    minimum_amount=int(os.getenv("MINIMUM_AMOUNT")),
    wait_seconds=int(os.getenv("SECONDS_TO_WAIT", 86400)),
    add_to_watchlist=bool(int(os.getenv("ADD_TO_WATCHLIST", "1"))),
    create_collections=bool(int(os.getenv("CREATE_COLLECTIONS", "1"))),
)

requests.packages.urllib3.disable_warnings()
logger = logging.getLogger()
logging.basicConfig(level=logging.INFO)

def create_collection(plex, items, description, library, mediatype, collection_title):
    """
    Create or update a collection in Plex based on media type and library.
    """
    logging.info(f"Creating or updating collection '{collection_title}' in library '{library.title}'...")
    item_list = []
    for item in items:
        search_results = plex.search(item.strip(), mediatype=mediatype, limit=3)
        if search_results:
            item_list.append(search_results[0])
            logging.info(f"{item} - found")
        else:
            logging.info(f"{item} - not found")

    if len(item_list) > userInputs.minimum_amount:
        try:
            collection = library.collection(collection_title)
            collection.removeItems(collection.items())
            collection.addItems(item_list)
            collection.editSummary(description)
            logging.info(f"Updated pre-existing collection: {collection_title}")
        except Exception as e:
            logging.info(f"Creating new collection: {collection_title}")
            collection = plex.createCollection(
                title=collection_title,
                section=library.title,
                items=item_list
            )
            collection.editSummary(description)
            logging.info(f"Added new collection: {collection_title}")
    else:
        logging.info(f"Not enough items were found to create or update the collection: {collection_title}")

def add_to_watchlist(plex, recommendations, mediatype):
    """
    Add recommended items to the user's Plex watchlist.
    """
    account = plex.myPlexAccount()
    logging.info("Adding recommendations to watchlist...")
    for title in recommendations:
        try:
            search_results = account.search(title.strip(), mediatype=mediatype)
            if search_results:
                item = search_results[0]
                item.addToWatchlist()
                logging.info(f"Added to watchlist: {title}")
            else:
                logging.info(f"Not found in global Plex database: {title}")
        except NotFound:
            logging.warning(f"Item not found: {title}")
        except Exception as e:
            logging.error(f"Failed to add {title} to watchlist: {e}")

def run():
     while True:
        logger.info("Starting collection run")
        try:
            session = requests.Session()
            session.verify = False
            plex = PlexServer(userInputs.plex_url, userInputs.plex_token, session=session)
            logging.info("Connected to Plex server")
        except Exception as e:
            logging.error("Plex Authorization error", exc_info=e)
            return

        try:
            all_recommendations = {}
            for library_name in userInputs.library_names:
                library = plex.library.section(library_name)
                mediatype = "show" if library.type == "show" else "movie"
                logging.info(f"Processing library: {library_name} (type: {mediatype})")
                
                history_items_titles = []
                account_id = plex.systemAccounts()[0].accountID
                logging.info(f"Fetching watch history for library: {library_name}")
                watch_history_items = plex.history(
                    librarySectionID=library.key, 
                    maxresults=userInputs.history_amount, 
                    accountID=account_id
                )
                for history_item in watch_history_items:
                    history_items_titles.append(history_item.title)

                logging.info(f"Fetching all items from library: {library_name}")
                library_contents = fetch_library_contents(library)
                combined_items_string = ", ".join(history_items_titles + library_contents)

                query = (
                    f"Based on the following {'shows' if mediatype == 'show' else 'movies'} I've watched: "
                    f"{combined_items_string}. "
                    "Please provide new and unique recommendations that are not in this list. "
                    f"I need around {userInputs.recommended_amount}. "
                    "Format your response as a comma-separated list of titles, followed by '+++' and a brief explanation of your recommendations. "
                    "Do not include any titles from the input list in your response."
                )

                try:
                    logging.info(f"Querying OpenAI for recommendations for library: {library_name}")
                    client = OpenAI(api_key=userInputs.openai_key)
                    chat_completion = client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": query}]
                    )
                    ai_result = chat_completion.choices[0].message.content
                    logging.info(f"AI response for library '{library_name}': {ai_result}")
                    ai_result_split = ai_result.split("+++")
                    recommendations = list(filter(None, ai_result_split[0].split(",")))
                    description = ai_result_split[1] if len(ai_result_split) > 1 else ""
                    all_recommendations[library_name] = (recommendations, description)
                except Exception as e:
                    logging.error(f"OpenAI query failed for library '{library_name}'", exc_info=e)

        except Exception as e:
            logging.error("Error during library processing", exc_info=e)
            return

        for library_name, (recommendations, description) in all_recommendations.items():
            if recommendations:
                library = plex.library.section(library_name)
                mediatype = "show" if library.type == "show" else "movie"
                collection_title = f"{userInputs.collection_title} - {library_name}"

                if userInputs.create_collections:
                    create_collection(plex, recommendations, description, library, mediatype, collection_title)

                if userInputs.add_to_watchlist:
                    add_to_watchlist(plex, recommendations, mediatype)

        logging.info("Waiting on next call...")
        time.sleep(userInputs.wait_seconds)

if __name__ == '__main__':
    run()