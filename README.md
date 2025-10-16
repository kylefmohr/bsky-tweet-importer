# Tweet Importer

This web service allows you to import your old tweets from Twitter's "request a copy of your data" feature into Bluesky.

## How to run

1.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
2.  **Run the web service:**
    ```bash
    python importer/main.py
    ```
3.  **Open your browser:**
    Navigate to `http://127.0.0.1:5000` in your web browser.

## How to use

1.  **Login to Bluesky:**
    Enter your Bluesky handle and an app password. It is highly recommended to use an app password for security.
2.  **Upload your `tweets.js` file:**
    Click the "Upload" button and select the `tweets.js` file from your Twitter data archive.
3.  **Select tweets to import:**
    You will be shown a list of your tweets. You can select all, deselect all, or select individual tweets to import.
4.  **Import selected tweets:**
    Click the "Import Selected Tweets" button to begin the import process. The imported tweets will have the same timestamp as the original tweets.
