"""Unsupervised: group similar user questions to find recurring concerns and gaps in the knowledge base.

Data: user questions from saved assistant conversations (ChatMessage role=user), de-identified BEFORE clustering -
numbers, e-mails, phone numbers and capitalised names are masked and no user / conversation id is kept. If there are
too few saved questions, the project's own example questions are used so the pipeline can still be checked.

Method and why:
  * TF-IDF (word 1-2-grams) + truncated SVD (LSA, 50 dims): sparse short texts, CPU only; SVD removes noise and
    makes distances meaningful for clustering.
  * K-means for k in a small range, k chosen by the silhouette score (how much closer each question is to its own
    cluster than to the next one; -1..1, higher is better). K-means suits "group into topics" with a fixed count.
  * Agglomerative (Ward) clustering at the chosen k as a cross-check; agreement between the two (adjusted Rand
    index) tells whether the groups are stable or an artefact of one algorithm.
  * Near-duplicate detection: pairs with cosine similarity >= 0.9.
DBSCAN was not used: on short TF-IDF texts most points become "noise" without careful eps tuning per dataset.

Output is a REPORT for people (top terms + example questions per cluster). Clusters are topics, not diagnoses, and
are not used automatically by the assistant.
"""
import re

import numpy as np
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import Normalizer

_MASKS = [(re.compile(r"\S+@\S+"), "<email>"), (re.compile(r"\+?\d[\d\s-]{6,}\d"), "<phone>"),
          (re.compile(r"\b\d+([.,]\d+)?\b"), "<num>"),
          (re.compile(r"(?<=[a-z.,!?] )[A-Z][a-z]{2,}\b"), "<name>")]


def deidentify(text):
    t = text
    for rx, repl in _MASKS:
        t = rx.sub(repl, t)
    return t.strip()


def run(texts, k_range=range(3, 13), seed=13):
    docs = sorted({deidentify(t).lower() for t in texts if t and t.strip()})
    if len(docs) < 10:
        raise ValueError("Need at least 10 distinct questions to cluster.")
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1, stop_words="english", sublinear_tf=True)
    x = vec.fit_transform(docs)
    dims = max(2, min(50, x.shape[1] - 1, len(docs) - 1))
    z = Normalizer().fit_transform(TruncatedSVD(n_components=dims, random_state=seed).fit_transform(x))
    scores = {}
    for k in k_range:
        if k >= len(docs):
            break
        labels = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(z)
        scores[k] = float(silhouette_score(z, labels))
    best_k = max(scores, key=scores.get)
    km = KMeans(n_clusters=best_k, n_init=10, random_state=seed).fit(z)
    agg = AgglomerativeClustering(n_clusters=best_k, linkage="ward").fit_predict(z)
    terms = np.array(vec.get_feature_names_out())
    clusters = []
    for c in range(best_k):
        idx = np.where(km.labels_ == c)[0]
        centroid = np.asarray(x[idx].mean(axis=0)).ravel()
        clusters.append({"cluster": c, "size": int(len(idx)), "top_terms": terms[centroid.argsort()[::-1][:6]].tolist(),
                         "examples": [docs[i] for i in idx[:5]]})
    clusters.sort(key=lambda c: -c["size"])
    sim = cosine_similarity(x)
    dup = [(docs[i], docs[j], round(float(sim[i, j]), 3)) for i in range(len(docs)) for j in range(i + 1, len(docs))
           if sim[i, j] >= 0.9][:20]
    return {"questions": len(docs), "silhouette_by_k": {str(k): round(v, 3) for k, v in scores.items()},
            "chosen_k": best_k, "silhouette": round(scores[best_k], 3),
            "kmeans_vs_ward_adjusted_rand": round(float(adjusted_rand_score(km.labels_, agg)), 3),
            "clusters": clusters, "near_duplicates": dup}
