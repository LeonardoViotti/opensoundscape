import pymongo
import pickle
import pandas as pd
import numpy as np
from scipy.ndimage import median_filter
from scipy.sparse import csr_matrix


def write_spectrogram(label, df, spec, normal, config):
    '''Write spectrogram to MongoDB

    Open connection to MongoDB and write the bounding box DataFrame,
    spectrogram (compressed sparse row 2D matrix), and normalization
    factor.  The DataFrame and spectrogram are pickled to reduce size

    Args:
        label: The label for the MongoDB entry (the filename)
        df: The bounding box DataFrame
        spec: The numpy 2D matrix containing the spectrogram
        normal: The np.max() of the original spectrogram
        config: The openbird configuration

    Returns:
        Nothing.
    '''

    with pymongo.MongoClient(config['db_uri']) as client:
        db = client[config['db_name']]
        coll = db[config['db_collection_name']]

        # Pickle Up the DF
        df_bytes = pickle.dumps(df)

        # Steps:
        # 1. Set the lowest 5% values to zero
        # 2. Store as compressed sparse row matrix
        # 3. Pickle and store
        if config.getboolean('db_sparse'):
            spec[spec <
                    (config.getint('db_sparse_thresh_percent') / 100.)] = 0
            spec_bytes = pickle.dumps(csr_matrix(spec))
        else:
            spec_bytes = pickle.dumps(spec)

        # Update existing, or insert
        coll.update_one({'label': label}, {'$set': {'label': label, 'df': df_bytes,
            'spectrogram': spec_bytes, 'normalization_factor': float(normal)}},
            upsert=False)

def read_spectrogram(label, config):
    '''Read spectrogram from MongoDB

    Open connection to MongoDB and read the bounding box DataFrame, spectrogram
    (compressed sparse row 2D matrix), and normalization factor. The DataFrame
    and spectrogram are pickled to reduce size

    Args:
        label: The label for the MongoDB entry (the filename)
        config: The openbird configuration, need the uri and names

    Returns:
        Tuple containing dataframe, spectrogram (dense representation), and
        normalization factor
    '''

    with pymongo.MongoClient(config['db_uri']) as client:
        db = client[config['db_name']]
        coll = db[config['db_collection_name']]

        # Extract DF and Spectrogram
        item = coll.find_one({'label': label})
        df_bytes = item['df']
        spec_bytes = item['spectrogram']
        normal = item['normalization_factor']
        
        # Recreate Data
        df = pd.DataFrame(pickle.loads(df_bytes))
        spec = pickle.loads(spec_bytes)
        if config.getboolean('db_sparse'):
            spec = spec.todense()

    return df, spec, normal
