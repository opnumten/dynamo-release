import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.neighbors import NearestNeighbors
from sklearn.manifold import TSNE
from scipy.stats import norm

from .psl import *

def reduceDimension(adata, n_pca_components = 25, n_components = 2, normalize_components = True, n_neighbors = 10, reduction_method='PSL', velocity_key = 'velocity'): # c("UMAP", 'tSNE', "DDRTree", "ICA", 'none')
    """Compute a low dimension reduction projection of an annodata object first with PCA, followed by non-linear dimension reduction methods

    Arguments
    ---------
    adata: :class:`~anndata.AnnData`
        an Annodata object 
    n_pca_components: 'int' (optional, default 50)
        Number of PCA components.  
    n_components: 'int' (optional, default 50)
        The dimension of the space to embed into.
    normalize_components: 'bool' (optional, default True)
        Whether to normalize the component before dimension reduction. 
    n_neighbors: 'int' (optional, default 10)
        Number of nearest neighbors when constructing adjacency matrix. 
    reduction_method: 'str' (optional, default PSL)
        Non-linear dimension reduction method to further reduce dimension based on the top n_pca_components PCA components. Currently, PSL 
        (probablistic structure learning, a new dimension reduction by us), tSNE or UMAP are supported. 
    velocity_key: 'str' (optional, default velocity)
        The dictionary key that corresponds to the estimated velocity values. 

    Returns
    -------
    Returns an updated `adata` with reduced dimension data for spliced counts, projected future transcript counts 'Y_dim' and adjacency matrix when possible.
    """

    n_obs = adata.shape[0]

    X = adata.layers['spliced']

    if(not 'X_pca' in adata.obsm.keys()):
        transformer = TruncatedSVD(n_components=n_pca_components, random_state=0)
        X_pca = transformer.fit(X.T).components_.T
        adata.obsm['X_pca'] = X_pca
    else:
        X_pca = adata.obsm['X_pca']

    if reduction_method is 'tSNE':
        bh_tsne = TSNE(n_components = n_components)
        X_dim = bh_tsne.fit_transform(X_pca)
        adata.obsm['X_tSNE'] = X_dim
    elif reduction_method is 'UMAP':
        import umap
        X_umap = umap.UMAP(n_components = n_components, n_neighbors = n_neighbors).fit(X_pca)
        X_dim = X_umap.embedding_
        adata.obsm['X_umap'] = X_dim
        adj_mat = X_umap.graph_
        adata.uns['UMAP_adj_mat'] = adj_mat
    elif reduction_method is 'PSL':
        adj_mat, X_dim = psl_py(X_pca, d = n_components, K = n_neighbors) # this need to be updated
        adata.obsm['X_psl'] = X_dim
        adata.uns['PSL_adj_mat'] = adj_mat

    # use both existing data and predicted future states in dimension reduction to get the velocity plot in 2D
    # use only the existing data for dimension reduction and then project new data in this reduced dimension
    if reduction_method is not 'UMAP':
        if n_neighbors is None: n_neighbors = int(n_obs / 50)
        nn = NearestNeighbors(n_neighbors=n_neighbors, n_jobs=-1) 
        nn.fit(X)
        dists, neighs = nn.kneighbors(X + adata.layers[velocity_key])
    elif reduction_method is 'UMAP':
        dists, neighs = X_umap.graph_, X_umap.graph_

    scale = np.median(dists, axis=1)
    weight = norm.pdf(x = dists, scale=scale[:, None])
    p_mass = weight.sum(1)
    weight = weight / p_mass[:, None]

    # calculate the embedding for the predicted future states of cells using a Gaussian kernel
    Y_dim = (X_dim[neighs] * (weight[:, :, None])).sum(1)

    adata.obsm['Y_dim'] = Y_dim

    return adata
