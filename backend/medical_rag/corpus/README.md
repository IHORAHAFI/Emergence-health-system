# Medical knowledge corpus

Plain-language summaries used by the AI medical assistant (retrieval-augmented generation).
Load them into the database with:

    python manage.py ingest_medical_docs            # add / update documents
    python manage.py ingest_medical_docs --check-urls   # also check that every source link answers

## Rules for authors

* Write **summaries in your own words** from a trusted public source. Do not paste copyrighted text.
  Preferred sources: WHO, Rwanda Biomedical Centre (RBC), NIH / MedlinePlus, CDC, NHS, national guidelines.
* Fill in `source_published` and `source_version` from the source page (leave empty if the page shows none -
  never guess a date).
* General information only: **no personal doses, no prescribing, no "stop your medicine" advice.**
* Every new or changed document is stored as *Pending clinical review*. A healthcare professional approves it
  in Django admin (Medical information assistant -> Knowledge documents). Answers say when a source is
  still pending review.
* Use `## Heading` sections. Each section becomes one searchable chunk.

## File format

Each file holds one or more documents. Each document starts with a front-matter block:

    ---
    id: malaria                      # unique, lowercase, used as slug
    title: Malaria
    category: infectious             # see KnowledgeDocument.Category
    kind: condition                  # condition | symptom | terminology | vital_signs | medicine | first_aid | prevention
    source_name: WHO fact sheet - Malaria
    source_url: https://www.who.int/news-room/fact-sheets/detail/malaria
    source_published:                # YYYY-MM-DD, or empty
    source_version:                  # e.g. "2024 update", or empty
    keywords: plasmodium mosquito    # extra search words, including lay terms
    emergency: false                 # true = topic is itself an emergency
    ---
    ## Definition
    ...
