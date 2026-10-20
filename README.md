# Proverse social posting
Scheduled Instagram + Facebook carousel posting for Proverse. Media in `media/<id>/v<N>/` is served by GitHub Pages
(Meta fetches it by URL). `carousel-plan.csv` is the queue; `.github/workflows/post.yml` runs `tools/post_carousels.py --go`
every 15 minutes and commits the updated queue. Managed from ~/Projects/proverse-carousels.
