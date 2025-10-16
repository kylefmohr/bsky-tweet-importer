import sys
import os
import tempfile
import shutil
import time
import logging

import json
from flask import Flask, render_template, request, redirect, url_for, session, Response, stream_with_context
from pysky.client import BskyClient as Client
from pysky.database import db
from pysky.bin.create_tables import create_non_existing_tables
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Replace with a real secret key

# Configure logging
logging.basicConfig(level=logging.INFO)

def get_tweets_from_session():
    if 'tweets_filepath' in session:
        filepath = session['tweets_filepath']
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                tweets_str = content[content.find('=') + 1:].strip()
                return json.loads(tweets_str)
    return None

def cleanup_temp_tweet_file():
    # Clean up tweets.js file
    if 'tweets_filepath' in session:
        filepath = session.pop('tweets_filepath', None)
        if filepath and os.path.exists(filepath):
            try:
                dirpath = os.path.dirname(filepath)
                os.remove(filepath)
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except OSError as e:
                logging.error(f"Error cleaning up tweet file: {e}")

@app.route('/')
def index():
    if 'handle' in session:
        return redirect(url_for('upload'))
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    create_non_existing_tables(db)
    handle = request.form['handle']
    password = request.form['password']
    try:
        client = Client(bsky_auth_username=handle, bsky_auth_password=password)
        client.get_user_profile(handle)
        session['handle'] = handle
        session['password'] = password
        return redirect(url_for('upload'))
    except Exception as e:
        return render_template('index.html', error=str(e))

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if 'handle' not in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        if 'file' not in request.files:
            return render_template('index.html', error='No file part')
        file = request.files['file']
        if file.filename == '':
            return render_template('index.html', error='No selected file')
        if file and file.filename.endswith('.js'):
            temp_dir = tempfile.mkdtemp()
            filepath = os.path.join(temp_dir, file.filename)
            file.save(filepath)
            session['tweets_filepath'] = filepath
            return redirect(url_for('select_tweets'))
    return render_template('index.html', page='upload')

@app.route('/select_tweets')
def select_tweets():
    if 'handle' not in session:
        return redirect(url_for('index'))
    
    tweets = get_tweets_from_session()
    if tweets is None:
        return redirect(url_for('upload'))

    return render_template('index.html', tweets=tweets, page='select_tweets')

@app.route('/start_import', methods=['POST'])
def start_import():
    if 'handle' not in session:
        return Response(json.dumps({'error': 'Unauthorized'}), status=401, mimetype='application/json')

    form_indices = request.form.getlist('tweet_indices')
    inversion = request.form.get('inversion') == 'true'

    if not form_indices and not inversion:
        return Response(json.dumps({'error': 'No tweets selected'}), status=400, mimetype='application/json')

    all_tweets = get_tweets_from_session()
    if all_tweets is None:
        return Response(json.dumps({'error': 'No tweets found in session'}), status=400, mimetype='application/json')

    if inversion:
        total_indices = set(map(str, range(len(all_tweets))))
        unselected_indices = set(form_indices)
        selected_indices = list(total_indices - unselected_indices)
    else:
        selected_indices = form_indices

    if not selected_indices:
        return Response(json.dumps({'error': 'No tweets to import after processing selection.'}), status=400, mimetype='application/json')

    if len(selected_indices) > 11666:
        return Response(json.dumps({'error': 'You cannot import more than 11,666 tweets at a time.'}), status=400, mimetype='application/json')

    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json', encoding='utf-8') as f:
            json.dump(selected_indices, f)
            indices_filepath = f.name
    except Exception as e:
        logging.error(f"Failed to create temporary indices file: {e}")
        return Response(json.dumps({'error': 'Could not create temporary file for import.'}), status=500, mimetype='application/json')

    return Response(json.dumps({'indices_filepath': indices_filepath}), status=200, mimetype='application/json')

@app.route('/import_tweets', methods=['GET'])
def import_tweets():
    if 'handle' not in session:
        return Response(json.dumps({'error': 'Unauthorized'}), status=401, mimetype='application/json')

    indices_filepath = request.args.get('indices_filepath')
    
    # Security check: ensure the file is in the system's temp directory
    if not indices_filepath or not os.path.abspath(indices_filepath).startswith(tempfile.gettempdir()):
        return Response(json.dumps({'error': 'Invalid or missing indices file path.'}), status=400, mimetype='application/json')

    def generate_importer(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                selected_tweets_indices = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            yield f"data: {json.dumps({'error': f'Could not read or parse indices file: {e}'})}\n\n"
            return

        tweets = get_tweets_from_session()
        if tweets is None:
            yield f"data: {json.dumps({'error': 'No tweets found in session'})}\n\n"
            return

        tweets_to_import = [tweets[int(i)] for i in selected_tweets_indices]
        successful_imports_count = 0
        last_successful_tweet = ""
        total_tweets = len(tweets_to_import)

        sleep_duration = 0
        if total_tweets >= 11666:
            sleep_duration = 7.5
        elif total_tweets >= 1666:
            sleep_duration = 2.2

        try:
            client = Client(bsky_auth_username=session['handle'], bsky_auth_password=session['password'])
            params = {
                "repo": client.did,
                "collection": "app.bsky.feed.post",
                "record": {}
            }

            for i, tweet_data in enumerate(tweets_to_import):
                tweet = tweet_data['tweet']
                text = tweet['full_text']
                created_at_str = tweet['created_at']
                
                progress_data = {
                    'current': i + 1,
                    'total': total_tweets,
                    'last_success': last_successful_tweet,
                    'next_tweet': text
                }
                yield f"data: {json.dumps(progress_data)}\n\n"

                if len(text) > 3000:
                    logging.warning(f"Tweet is too long to import, skipping: {text[:50]}...")
                    continue

                created_at = datetime.strptime(created_at_str, '%a %b %d %H:%M:%S %z %Y')
                params['record']['$type'] = 'app.bsky.feed.post'
                params['record']['text'] = text
                params['record']['createdAt'] = created_at.isoformat()

                retries = 4
                for j in range(retries):
                    try:
                        client.post(hostname='bsky.social', endpoint='xrpc/com.atproto.repo.createRecord', params=params)
                        successful_imports_count += 1
                        last_successful_tweet = text
                        logging.info(f"Successfully imported tweet: {text[:50]}...")
                        if sleep_duration > 0:
                            time.sleep(sleep_duration)
                        break
                    except Exception as e:
                        wait = 2 ** j
                        logging.error(f"Error importing tweet: {e}. Retrying in {wait} seconds...")
                        time.sleep(wait)
                else: # No break
                    logging.error(f"Failed to import tweet after {retries} retries: {text[:50]}...")

            yield f"data: {json.dumps({'final': True, 'successful': successful_imports_count, 'total': total_tweets})}\n\n"

        except Exception as e:
            logging.error(f"An unexpected error occurred during import: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            # Clean up the indices file and the main tweet file
            if os.path.exists(filepath):
                os.remove(filepath)
            cleanup_temp_tweet_file()

    return Response(stream_with_context(generate_importer(indices_filepath)), mimetype='text/event-stream')

@app.route('/logout')
def logout():
    cleanup_temp_tweet_file()
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)