# AI resources: datasets, models and books

Checked 28 November 2026. Licences were checked on the provider's own page or licence file where stated; **re-check
before any commercial use**. Only CLINC150 has been downloaded and used so far.

## Used now

| Resource | Provider | Licence | Format / size | Used for | Limitations |
|---|---|---|---|---|---|
| [CLINC150 (oos-eval)](https://github.com/clinc/oos-eval) | Clinc / Larson et al., EMNLP 2019 | CC BY 3.0 (verified in the repository's LICENSE file; [UCI](https://archive.ics.uci.edu/dataset/570/clinc150) lists CC BY 4.0) | JSON, 2.5 MB, 23,700 utterances | Intent classifier: greetings, thanks, goodbye, "what can you do", out-of-scope | English only, general domain, no medical content |
| Project knowledge base (`medical_rag/corpus`) | Summaries of WHO, CDC, NHS, MedlinePlus, RBC pages, written by the project | Own text; each document records its source URL | Markdown, 68 documents | Retrieval (RAG) and synthetic medical questions for the classifier | Summaries need clinical review (status shown per document) |

## Recommended next (not downloaded)

### Medical text
| Resource | Licence | Suitability | Limitations |
|---|---|---|---|
| [MedQuAD](https://github.com/abachaa/MedQuAD) – 47,457 Q&A pairs from 12 NIH websites | No licence file confirmed; the [Hugging Face copy](https://huggingface.co/datasets/lavita/MedQuAD) removed 31,034 answers for MedlinePlus copyright | Question **types** (treatment, symptoms…) could train a medical sub-intent classifier; answers should NOT be copied into the knowledge base | Check the licence before use; US-centred |
| [WHO publications](https://www.who.int/about/policies/publishing/copyright) | CC BY-NC-SA 3.0 IGO by default (non-commercial, credit WHO, share alike); some items differ | Best source for new knowledge-base summaries (malaria, TB, first aid) | Commercial use needs permission; third-party figures excluded |

### Speech
| Resource | Licence | Suitability | Limitations |
|---|---|---|---|
| [Mozilla Common Voice – Kinyarwanda](https://datacollective.mozillafoundation.org/datasets/cmn60xnfi00wnnv07028xltoz) (v25, March 2026) | CC0-1.0 | About 2,384 h (2,002 h validated), 1,181 speakers – for evaluating/training Kinyarwanda speech recognition | ~57 GB; needs a GPU to train; read speech, not medical vocabulary |
| [NVIDIA Conformer-CTC Large (Kinyarwanda)](https://huggingface.co/nvidia/stt_rw_conformer_ctc_large) | CC BY 4.0 | Ready-made Kinyarwanda speech-to-text (test WER 18.22 on Common Voice 9) for a server-side option | 120 M parameters, NeMo/PyTorch; too heavy for this laptop (no CUDA GPU, 7.7 GB RAM) |

The app currently uses the phone's own speech service (`speech_to_text`, on-device first). Kinyarwanda works
only where the phone provides it.

### Sound
| Resource | Licence | Decision |
|---|---|---|
| [ESC-50](https://github.com/karolpiczak/ESC-50) – 2,000 clips, 50 classes incl. siren | CC BY-NC 3.0 (non-commercial); the ESC-10 subset is CC BY but excludes siren | **Not implemented.** No feature needs it: the app already plays sirens and does not need to recognise them. Phone-microphone sound detection cannot reliably prove an emergency and would need continuous listening. Revisit only with a concrete use case and explicit consent controls. |

## Books (free and legal)
| Book | Access / licence | Topic |
|---|---|---|
| Sutton & Barto, *Reinforcement Learning: An Introduction* (2nd ed., MIT Press 2018) | Free PDF from the [authors' site](http://incompleteideas.net/book/the-book-2nd.html); CC BY-NC-ND 2.0 | Bandits (ch. 2) and why offline evaluation matters |
| Jurafsky & Martin, *Speech and Language Processing* (3rd ed. draft, Aug 2026) | [Free draft](https://web.stanford.edu/~jurafsky/slp3/) | Text classification, intent, speech recognition, dialogue |
| scikit-learn User Guide | [scikit-learn.org](https://scikit-learn.org/stable/user_guide.html), BSD | Logistic regression, TF-IDF, k-means, silhouette |
