r"""Seed Neon Postgres with sample lawyer directory data.

Usage (from legal_assist_backend, with the venv):
    .\.venv\Scripts\python.exe scripts\seed_lawyer_directory.py            # seed if empty
    .\.venv\Scripts\python.exe scripts\seed_lawyer_directory.py --force    # wipe + reseed

Creates two tables:
    lawyers        — profile, experience, fees, rating, availability
    lawyer_reviews — client reviews linked to lawyers
"""

import logging
import sys
from pathlib import Path

# Make the project root importable when running as `python scripts/...`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from connectors.neon_postgres import get_pg_conn, run_select  # noqa: E402

logger = logging.getLogger("legal_assist.scripts.seed")

DDL = """
CREATE TABLE IF NOT EXISTS lawyers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    specialisation TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    experience_years INTEGER NOT NULL,
    bar_council_id TEXT NOT NULL UNIQUE,
    fees_per_hearing NUMERIC(10,2) NOT NULL,
    rating NUMERIC(3,2) NOT NULL,
    reviews_count INTEGER NOT NULL DEFAULT 0,
    available_for_chat BOOLEAN NOT NULL DEFAULT FALSE,
    languages TEXT NOT NULL DEFAULT '',
    profile TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lawyer_reviews (
    id SERIAL PRIMARY KEY,
    lawyer_id INTEGER NOT NULL REFERENCES lawyers(id) ON DELETE CASCADE,
    client_name TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT NOT NULL,
    reviewed_at DATE NOT NULL
);
"""

LAWYERS = [
    ("Adv. Rajesh Kumar", "Criminal Law", "Delhi", "Delhi", 18, "D/1234/2008", 7500, 4.7, 62, True,
     "Hindi, English",
     "Senior criminal defence counsel practising before the Delhi High Court and "
     "Saket Sessions Court. Handles bail matters, white-collar crime, and NDPS cases. "
     "Known for aggressive cross-examination and quick bail hearings."),
    ("Adv. Priya Sharma", "Family Law", "Mumbai", "Maharashtra", 11, "M/5678/2015", 5000, 4.8, 47, True,
     "Hindi, English, Marathi",
     "Family law specialist handling divorce, child custody, and maintenance matters "
     "in Mumbai family courts. Mediation-first approach; settles most matters without trial."),
    ("Adv. Mohammed Ali", "Criminal Law", "Bangalore", "Karnataka", 15, "B/9012/2010", 6500, 4.6, 39, False,
     "English, Kannada, Urdu",
     "Criminal appellate lawyer with a strong record in the Karnataka High Court. "
     "Focus areas: anticipatory bail, quashing petitions, and cybercrime defence."),
    ("Adv. Sunita Reddy", "Property Law", "Hyderabad", "Telangana", 14, "H/3456/2011", 5500, 4.5, 33, True,
     "Telugu, Hindi, English",
     "Property and real-estate disputes — title verification, partition suits, "
     "RERA complaints, and land acquisition challenges before Telangana courts."),
    ("Adv. Vikram Singh", "Corporate Law", "Delhi", "Delhi", 21, "D/7890/2004", 12000, 4.9, 88, True,
     "Hindi, English",
     "Corporate and commercial litigation partner-level counsel. Handles NCLT matters, "
     "shareholder disputes, contract enforcement, and arbitration for startups and SMEs."),
    ("Adv. Ananya Iyer", "Employment Law", "Chennai", "Tamil Nadu", 8, "C/2345/2018", 4000, 4.4, 25, True,
     "Tamil, English",
     "Advises employees and employers on wrongful termination, POSH compliance, "
     "gratuity and PF disputes. Appears before Chennai labour courts."),
    ("Adv. Arjun Mehta", "Tax Law", "Mumbai", "Maharashtra", 16, "M/6789/2009", 9000, 4.6, 41, False,
     "Hindi, English, Gujarati",
     "Direct and indirect tax litigation — income tax appeals, GST disputes, "
     "and penalty proceedings before ITAT and the Bombay High Court."),
    ("Adv. Kavita Joshi", "Family Law", "Delhi", "Delhi", 9, "D/4567/2017", 3500, 4.3, 21, True,
     "Hindi, English",
     "Handles mutual-consent divorce, domestic violence (DV Act) cases, and "
     "guardianship petitions. Affordable fees; legal-aid friendly."),
    ("Adv. Rohan Banerjee", "Civil Law", "Kolkata", "West Bengal", 12, "K/8901/2013", 4500, 4.5, 30, True,
     "Bengali, Hindi, English",
     "Civil suits, recovery of money, injunctions, and specific performance "
     "of contracts in Kolkata civil courts and the Calcutta High Court."),
    ("Adv. Deepa Nair", "Intellectual Property", "Bangalore", "Karnataka", 10, "B/1357/2015", 7000, 4.7, 28, True,
     "English, Malayalam, Kannada",
     "Trademark and copyright enforcement, patent oppositions, and IP licensing. "
     "Works extensively with tech startups and creators."),
    ("Adv. Suresh Patel", "Property Law", "Ahmedabad", "Gujarat", 24, "G/2468/2001", 8000, 4.8, 71, False,
     "Gujarati, Hindi, English",
     "Veteran property lawyer — agricultural land conversion, society disputes, "
     "and builder-buyer litigation in Gujarat. Former government pleader."),
    ("Adv. Meera Kulkarni", "Consumer Law", "Pune", "Maharashtra", 7, "M/9753/2019", 3000, 4.2, 18, True,
     "Marathi, Hindi, English",
     "Consumer complaints — defective goods, insurance claims, e-commerce disputes, "
     "and medical negligence before Maharashtra consumer commissions."),
    ("Adv. Imran Qureshi", "Criminal Law", "Lucknow", "Uttar Pradesh", 13, "U/6543/2012", 4000, 4.4, 26, True,
     "Hindi, Urdu, English",
     "Criminal trial lawyer in Lucknow — bail, appeals, and POCSO defence. "
     "Regularly appears in UP district courts and the Allahabad High Court (Lucknow bench)."),
    ("Adv. Shalini Verma", "Cyber Law", "Delhi", "Delhi", 6, "D/3698/2020", 5000, 4.5, 15, True,
     "Hindi, English",
     "Cybercrime complaints, online fraud recovery, IT Act matters, and data-privacy "
     "advisory. Files and tracks complaints on the national cyber portal for clients."),
]

# (lawyer index 1-based into LAWYERS, client_name, rating, comment, date)
REVIEWS = [
    (1, "Amit S.", 5, "Got bail within 3 days. Very confident in court.", "2026-05-11"),
    (1, "Neha R.", 4, "Slightly expensive but worth it for serious matters.", "2026-03-02"),
    (1, "Rakesh P.", 5, "Handled my NDPS case brilliantly. Highly recommended.", "2026-06-19"),
    (2, "Sneha T.", 5, "Priya ma'am settled my custody matter amicably. Forever grateful.", "2026-04-08"),
    (2, "Vivek M.", 5, "Very patient, explains everything in simple language.", "2026-02-14"),
    (2, "Farah K.", 4, "Good outcomes, though hearings sometimes get rescheduled.", "2026-01-27"),
    (3, "Girish H.", 5, "My quashing petition was allowed. Excellent drafting.", "2026-05-30"),
    (3, "Lakshmi V.", 4, "Knowledgeable about cybercrime defence.", "2026-03-21"),
    (4, "Prasad G.", 5, "Cleared our land title issue after 6 years of litigation.", "2026-04-25"),
    (4, "Divya B.", 4, "RERA complaint was resolved in 8 months.", "2026-02-02"),
    (5, "StartupX Founder", 5, "Saved our shareholder agreement mess. Sharp negotiator.", "2026-06-01"),
    (5, "Karan J.", 5, "Best corporate counsel in Delhi for NCLT work.", "2026-05-15"),
    (5, "Ritu A.", 4, "Fees are high but the quality justifies them.", "2026-01-10"),
    (6, "Manoj E.", 4, "Recovered my unpaid gratuity through labour court.", "2026-03-18"),
    (6, "Swathi R.", 5, "Guided me through a wrongful termination case.", "2026-06-27"),
    (7, "Dharmesh S.", 5, "Won our GST appeal. Very thorough with numbers.", "2026-04-14"),
    (7, "Pooja N.", 4, "Great for ITAT appeals; response time could improve.", "2026-02-09"),
    (8, "Ramesh C.", 4, "Affordable and honest about case prospects.", "2026-05-05"),
    (8, "Alka D.", 5, "Mutual divorce completed smoothly in 7 months.", "2026-06-11"),
    (9, "Tanmay B.", 5, "Recovered Rs 12 lakh through a money recovery suit.", "2026-03-30"),
    (9, "Ishita S.", 4, "Solid civil lawyer, clear communication.", "2026-01-22"),
    (10, "AppWorks CTO", 5, "Protected our trademark in an infringement suit.", "2026-05-23"),
    (10, "Rahul K.", 4, "Good patent opposition work for our hardware startup.", "2026-04-03"),
    (11, "Bhavesh M.", 5, "24 years of experience shows. Builder had to refund us.", "2026-02-28"),
    (11, "Hetal P.", 5, "Converted our NA land paperwork flawlessly.", "2026-06-08"),
    (12, "Akshay W.", 4, "Got a refund from an e-commerce site via consumer forum.", "2026-05-19"),
    (13, "Zaid A.", 5, "Anticipatory bail granted quickly. Very responsive.", "2026-04-17"),
    (14, "Nikhil T.", 5, "Recovered money lost in an online trading scam.", "2026-06-24"),
    (14, "Shreya G.", 4, "Knows the cyber portal process inside out.", "2026-03-07"),
]


def seed(force: bool = False) -> None:
    with get_pg_conn(read_only=False) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute("SELECT COUNT(*) FROM lawyers")
            count = cur.fetchone()[0]
            if count and not force:
                print(f"lawyers already has {count} rows — use --force to reseed")
                conn.commit()
                return
            if count and force:
                cur.execute("TRUNCATE lawyer_reviews, lawyers RESTART IDENTITY CASCADE")
                print("wiped existing rows (--force)")

            for row in LAWYERS:
                cur.execute(
                    "INSERT INTO lawyers (name, specialisation, city, state, experience_years, "
                    "bar_council_id, fees_per_hearing, rating, reviews_count, available_for_chat, "
                    "languages, profile) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    row,
                )
            print(f"inserted {len(LAWYERS)} lawyers")

            for idx, client, rating, comment, date in REVIEWS:
                cur.execute(
                    "INSERT INTO lawyer_reviews (lawyer_id, client_name, rating, comment, reviewed_at) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (idx, client, rating, comment, date),
                )
            print(f"inserted {len(REVIEWS)} reviews")
            conn.commit()

    result = run_select("SELECT COUNT(*) AS n FROM lawyers")
    print("verification — lawyers in DB:", result["rows"][0]["n"])


if __name__ == "__main__":
    seed(force="--force" in sys.argv)
