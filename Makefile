.PHONY: all data web test verify clean

all: data web

data:          ## בניית מאגר הנתונים מקבצי המקור
	python3 tools/build_dataset.py

web: data      ## בניית עמוד המחשבון
	python3 tools/build_web.py

test:          ## הרצת כל הבדיקות
	python3 -m unittest discover -s tests -v

verify:        ## דוח אי-התאמות בין קבצי המקור
	python3 tools/verify_sources.py

clean:
	rm -f data/salary_data.json web/index.html
	find . -name __pycache__ -type d -exec rm -rf {} +
