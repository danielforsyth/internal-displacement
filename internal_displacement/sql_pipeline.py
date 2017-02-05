from internal_displacement.scraper import scrape
from internal_displacement.article import Article
from internal_displacement.csv_tools import urls_from_csv, csv_read
import os
import json
import pandas as pd
import dateutil
import sqlite3
import numpy as np
import concurrent
from concurrent import futures
import itertools

"""
A pipeline to facilitate the extraction and storage of data from URLS in an SQL database.
    - Facilitates ingestion of articles (which may or may not be labeled with a category)
    - Allow storage of features (ie text) extracted from articles
    - Allow storage of labels for training cases
    - Allow retrieval of labeled features for training.
    - Data is persisted to disk via SQL
    - Data can be retrieved from disk by initializing an URLArticlePipelineSQl object with the database file.
    - Uses two SQL tables
        - table 'Articles' containing information common to all articles (ie all features except labels)
        - table 'Labels'containing the labels for training cases
    - Both tables use URL as the primary key
        - ensures each url is unique in the database and is only scraped once.
        - training data can be retrieved by doing an inner join.
    -For now, SQLite3 is used. If we need to scale up we could swap this for psycopg2 and AWS etc.
"""


class URLArticlePipelineSQL(object):
    """
    Usage:
        Initialize an instance of URLArticlePipelineSQL with a filepath to the SQL database file.
            - If the file does not exist it will be created.
        Populate the SQL article database with scraped features using process_urls
        Populate the SQL label database with process_labeled_data.

        Retrieve numpy arrays of features and labels using get_training_data.
    """

    def __init__(self, sql_database_file):
        """
        :param sql_database_file:   The path to the sql database file
        """
        self.sql_connection = sqlite3.connect(sql_database_file, isolation_level=None)
        self.sql_cursor = self.sql_connection.cursor()
        self.sql_cursor.execute(
            "CREATE TABLE IF NOT EXISTS Articles (title TEXT, url TEXT,author TEXT,datetime TEXT,domain TEXT, content TEXT, content_type TEXT)")
        self.sql_cursor.execute("CREATE TABLE IF NOT EXISTS Labels (url TEXT,category TEXT)")

    def insert_article(self, article):
        """
        Inserts articles into the database.
        :param article:     An article object


        """
        url = article.url
        authors = ",".join(article.authors)
        pub_date = article.get_pub_date_string()
        domain = article.domain
        content = article.content
        content_type = article.content_type
        title = article.title
        try:
            self.sql_cursor.execute("INSERT INTO Articles VALUES (?,?,?,?,?,?,?)",
                                    (title, url, authors, pub_date, domain, content, content_type))
            self.sql_connection.commit()
        except sqlite3.IntegrityError:
            print("URL{url} already exists in article table. Skipping.".format(self.url))
        except Exception as e:
            print("Exception: {}".format(e))

    def process_urls(self, url_csv, url_column="URL"):
        """
        Given a csv file containing article urls ,
        populate the SQL table with the article data.


        :param url_csv:         The csv file containing the URLs (and maybe the labels)
        :param url_column:      The column containing the URLs
        """
        dataset = csv_read(url_csv)
        urls = urls_from_csv(dataset, url_column)
        existing_urls = [r[0] for r in self.sql_cursor.execute("SELECT url FROM Articles")]
        urls = [u for u in urls if u not in existing_urls]

        article_futures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for url in urls:
                article_futures.append(executor.submit(scrape, url))
            for f in concurrent.futures.as_completed(article_futures):
                try:
                    article = f.result()
                    if article is None:
                        continue
                    else:
                        print(article.title)
                        self.insert_article(article)
                except Exception as e:
                    print("Exception: {}".format(e))

    def process_labeled_data(self, csv_filepath, url_column_name="URL", label_column_name="Tag"):

        """
        Populates the Labels SQL table. URLs that are already present in the table will not be added again.
        :param csv_filepath: path to a csv file containing labeled URLS.
        :param url_column_name: a string containing the name of the URL column name
        :param label_column_name: a string containing the name of the label column name

        """
        df = pd.read_csv(csv_filepath) # For now just using pandas, but could replace with a custom function
        urls = list(df[url_column_name].values)
        existing_urls = [r[0] for r in self.sql_cursor.execute("SELECT url FROM Labels")]
        urls = [u for u in urls if u not in existing_urls]
        labels = list(df[label_column_name].values)
        values = list(zip(urls, labels))
        self.sql_cursor.executemany("INSERT INTO Labels VALUES (?, ?)", values)
        self.sql_connection.commit()

    def get_training_data(self):
        """
        Retrieves the labels and features for use in a classification task
        Returns:
            Two numpy arrays; one containing texts and one containing labels.
        """

        training_cases = self.sql_cursor.execute(
            "SELECT content,category FROM Articles INNER JOIN Labels ON Articles.url = Labels.url")
        labels = np.array([r[1] for r in training_cases])
        features = np.array(r[0] for r in training_cases)
        return labels, features


