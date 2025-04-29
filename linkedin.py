import json
import time
import random
import os
import re
import pandas as pd
import requests
import subprocess
import tempfile
import uuid
from datetime import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium_stealth import stealth
from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException, TimeoutException, StaleElementReferenceException
from webdriver_manager.chrome import ChromeDriverManager
from sqlalchemy import create_engine, Column, Integer, String, Text, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import asyncio
from bs4 import BeautifulSoup
import logging
from rnet import Client, Impersonate


TOKEN = '7844666863:AAF0fTu1EqWC1v55oC25TVzSjClSuxkO2X4'
chat_id = None
company_list = []
company_name_map = {}

temp_dir = os.path.join(tempfile.gettempdir(), f"selenium_{uuid.uuid4().hex}")
os.makedirs(temp_dir, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database setup
Base = declarative_base()
engine = create_engine('postgresql://postgres.gncxzrslsmbwyhefawer:tRIOI1iU59gyK1nk@aws-0-eu-west-2.pooler.supabase.com:6543/postgres')
Session = sessionmaker(bind=engine)


class Job(Base):
    __tablename__ = 'jobs'
    
    id = Column(Integer, primary_key=True)
    job_title = Column(String(255), nullable=False)
    company_name = Column(String(255), nullable=False)
    company_logo = Column(Text, nullable=True)
    salary = Column(String(100), nullable=True)
    posted_date = Column(Text, nullable=False)
    experience = Column(String(100), nullable=True)
    location = Column(String(255), nullable=True)
    apply_link = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    data_source = Column(String(180), nullable=False)

# Function to ensure database tables exist
def setup_database():
    """Create database tables if they don't exist"""
    try:
        Base.metadata.create_all(engine)
        logger.info("Database tables created successfully")
    except Exception as e:
        error_message = f"Database setup error: {str(e)}"
        logger.error(error_message)
        notify_failure(error_message, "setup_database")


def get_chat_id(TOKEN: str) -> str:
    """Fetches the chat ID from the latest Telegram bot update."""
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        response = requests.get(url)
        info = response.json()
        chat_id = info['result'][0]['message']['chat']['id']
        return chat_id
    except Exception as e:
        print(f"Failed to get chat ID: {e}")
        return None

def send_message(TOKEN: str, message: str, chat_id: str):
    """Sends a message using Telegram bot."""
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage?chat_id={chat_id}&text={message}"
        response = requests.get(url)
        if response.status_code == 200:
            print("Sent message successfully")
        else:
            print(f"Message not sent. Status code: {response.status_code}")
    except Exception as e:
        print(f"Failed to send message: {e}")

def notify_failure(error_message, location="Unknown"):
    """Sends a failure notification to Telegram."""
    global chat_id, TOKEN
    try:
        if chat_id is None:
            chat_id = get_chat_id(TOKEN)
        if chat_id:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"❌ LINKEDIN SCRAPER FAILURE at {timestamp}\nLocation: {location}\nError: {error_message}"
            send_message(TOKEN, message, chat_id)
    except Exception as e:
        print(f"Failed to send failure notification: {e}")

def clean_name(name):
    """Removes special characters from a company name, keeps the rest the same."""
    try:
        if not name:
            return ""
        name = str(name).strip()
        # Remove special characters but keep letters, numbers, and spaces
        name = re.sub(r'[^A-Za-z0-9\s]', '', name)
        return name.strip()
    except Exception as e:
        error_message = f"Failed to clean name: {str(e)}"
        notify_failure(error_message, "clean_name")
        return ""

def get_company_list():
    """Loads and cleans the list of target companies from CSV."""
    global company_list, company_name_map
    try:
        df = pd.read_csv(r"data/2025-04-04_-_Worker_and_Temporary_Worker.csv")
        df['Clean Name'] = df['Organisation Name'].apply(clean_name)
        company_list = list(df['Clean Name'])
        
        # Create a mapping from raw names to cleaned names for reference
        for idx, row in df.iterrows():
            original = row['Organisation Name']
            cleaned = row['Clean Name']
            company_name_map[cleaned] = original
            
        print(f"Loaded {len(company_list)} companies from CSV")
    except Exception as e:
        error_message = f"Failed to load company list: {str(e)}"
        notify_failure(error_message, "get_company_list")
        raise

def kill_chrome_processes():
    try:
        subprocess.run(["pkill", "-f", "chrome"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-f", "chromedriver"], stderr=subprocess.DEVNULL)
        time.sleep(2)  # Give OS time to release resources
    except Exception as e:
        print(f"Error killing processes: {e}")

def is_company_in_list(company_name):
    """Checks if a company name is in the target list using improved matching."""
    try:
        if not company_name:
            return False
        
        clean_company = clean_name(company_name)
        
        # Exact match
        if clean_company in company_list:
            return True
            
        # Try partial matching for companies with longer names
        for target_company in company_list:
            # If either name contains the other completely
            if clean_company in target_company or target_company in clean_company:
                # Only match if the contained part is substantial (at least 5 chars)
                if len(clean_company) >= 5 and len(target_company) >= 5:
                    print(f"Fuzzy match found: '{company_name}' matches '{company_name_map.get(target_company, target_company)}'")
                    return True
        
        return False
    except Exception as e:
        error_message = f"Failed to check company in list: {str(e)}"
        notify_failure(error_message, "is_company_in_list")
        return False

def remove_duplicates(jobs_list):
    """Removes duplicate job listings based on job URL."""
    try:
        unique_jobs = []
        seen_urls = set()
        
        for job in jobs_list:
            # Use job URL as unique identifier
            job_url = job.get('job_url')
            
            if not job_url:
                # If no URL (shouldn't happen), use combo of title and company
                job_identifier = f"{job.get('title', '')}_{job.get('company', '')}_{job.get('location', '')}"
            else:
                job_identifier = job_url
                
            if job_identifier not in seen_urls:
                seen_urls.add(job_identifier)
                unique_jobs.append(job)
        
        print(f"Removed {len(jobs_list) - len(unique_jobs)} duplicate jobs")
        return unique_jobs
    except Exception as e:
        error_message = f"Failed to remove duplicates: {str(e)}"
        notify_failure(error_message, "remove_duplicates")
        return jobs_list  # Return original list if deduplication fails

# Setup Chrome with Stealth
try:
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--lang=en-US")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f"--user-data-dir={temp_dir}")
    
    # For GitHub Actions, we need to specify these additional options
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(service=webdriver.ChromeService(ChromeDriverManager().install()), options=options)

    # Apply stealth settings
    stealth(driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True,
    )
except Exception as e:
    error_message = f"Failed to setup Chrome driver: {str(e)}"
    notify_failure(error_message, "Chrome setup")
    raise

# Helper function for human-like waiting
def human_delay(min_seconds=1, max_seconds=3):
    time.sleep(random.uniform(min_seconds, max_seconds))

try:
    # Initialize chat_id early
    chat_id = get_chat_id(TOKEN)
    
    # Load company list before starting
    get_company_list()
    
    URL = "https://www.linkedin.com/jobs/search/?currentJobId=4215342655&f_E=4&f_JT=F&f_SB2=42&f_TPR=r604800&f_WT=1%2C3&geoId=101165590&keywords=&location=United%20Kingdom&origin=JOB_SEARCH_PAGE_JOB_FILTER"
    driver.get(URL)
    human_delay(4, 7)

    # Wait for page to fully load
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".jobs-search__results-list"))
        )
        print("✅ Page loaded successfully")
    except TimeoutException:
        print("⚠️ Page took too long to load, but continuing anyway")


except Exception as e:
    error_message = f"Failed during initial setup and page loading: {str(e)}"
    notify_failure(error_message, "Initial setup")
    raise

def close_linkedin_popup():
    try:
        # Try the specific dismiss button you provided
        specific_dismiss_selectors = [
            "button.modal__dismiss.contextual-sign-in-modal__modal-dismiss",
            "button.modal__dismiss[aria-label='Dismiss']",
            "button.contextual-sign-in-modal__modal-dismiss",
            ".modal__dismiss.contextual-sign-in-modal__modal-dismiss",
            "button[aria-label='Dismiss']"
        ]
        
        for selector in specific_dismiss_selectors:
            try:
                dismiss_buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                for button in dismiss_buttons:
                    if button.is_displayed():
                        print(f"🎯 Found specific dismiss button: {selector}")
                        # Try JavaScript click which is more reliable
                        driver.execute_script("arguments[0].click();", button)
                        print("✅ Closed LinkedIn sign-in popup")
                        human_delay(1, 2)
                        return True
            except Exception as e:
                continue
        
        # Use the XPath approach as backup
        xpath_selectors = [
            "//button[contains(@class, 'modal__dismiss')]",
            "//button[@aria-label='Dismiss']",
            "//button[contains(@class, 'contextual-sign-in-modal__modal-dismiss')]"
        ]
        
        for xpath in xpath_selectors:
            try:
                elements = driver.find_elements(By.XPATH, xpath)
                for element in elements:
                    if element.is_displayed():
                        driver.execute_script("arguments[0].click();", element)
                        print(f"✅ Closed popup using XPath: {xpath}")
                        human_delay(1, 2)
                        return True
            except:
                continue
                
        # Last resort: try to use JavaScript to disable the modal directly
        driver.execute_script("""
            // Try to find and hide any modal or overlay
            var modals = document.querySelectorAll('.modal, .modal__overlay, [role="dialog"], .artdeco-modal, .contextual-sign-in-modal');
            modals.forEach(function(modal) {
                if (modal && modal.style.display !== 'none') {
                    modal.style.display = 'none';
                    console.log('Hidden modal via JS');
                }
            });
            
            // Remove potential overlay backdrop
            var backdrops = document.querySelectorAll('.modal__overlay, .artdeco-modal-overlay');
            backdrops.forEach(function(backdrop) {
                if (backdrop) {
                    backdrop.remove();
                    console.log('Removed backdrop via JS');
                }
            });
            
            // Remove body classes that might disable scrolling
            document.body.classList.remove('overflow-hidden');
        """)
        print("🔧 Attempted JavaScript modal removal")
        
        return False
    except Exception as e:
        print(f"⚠️ Error handling LinkedIn popup: {e}")
        return False

# General function to close other popups
def close_popups():
    try:
        # Try to close the LinkedIn signup modal first
        signup_closed = close_linkedin_popup()
        
        # Handle other types of popups
        popup_elements = [
            '.artdeco-toasts_toasts',
            '.artdeco-toast-item__dismiss',
            '.msg-overlay-bubble-header__controls button',
            '.consent-page button',
            '//button[contains(text(), "Dismiss")]',
            '//button[contains(text(), "Not now")]',
            '//button[contains(text(), "No, thanks")]'
        ]
        
        for selector in popup_elements:
            try:
                if selector.startswith('//'):
                    elements = driver.find_elements(By.XPATH, selector)
                else:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                
                for element in elements:
                    if element.is_displayed():
                        driver.execute_script("arguments[0].click();", element)
                        print(f"❌ Closed popup: {selector}")
                        human_delay(0.5, 1)
            except:
                continue
                
        # Use JavaScript as fallback for toasts
        driver.execute_script("""
            var toasts = document.querySelector('.artdeco-toasts_toasts');
            if (toasts) toasts.style.display='none';
            
            var overlays = document.querySelectorAll('.artdeco-modal');
            overlays.forEach(function(overlay) {
                if (overlay.style.display !== 'none') overlay.style.display='none';
            });
        """)
    except Exception as e:
        error_message = f"Error handling popups: {str(e)}"
        print(error_message)
        notify_failure(error_message, "close_popups")

import json
from bs4 import BeautifulSoup
import html

def extract_job_description(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    script_tag = soup.find("script", type="application/ld+json")
    
    if not script_tag:
        return None

    try:
        job_json = json.loads(script_tag.string)
        raw_description = job_json.get("description", "")
        # Convert HTML entities (e.g., &lt;p&gt;) to actual characters
        decoded_description = html.unescape(raw_description)
        return decoded_description.strip()
    except json.JSONDecodeError:
        return None

async def parse_url(url):
    client = Client(impersonate=Impersonate.Firefox136)
    resp = await client.get(url)
    print("Status Code: ", resp.status_code)
    
    content = await resp.text()

    soup = BeautifulSoup(content, "html.parser")
    description = extract_job_description(content)

    try:
        external_url = soup.find('code', id='applyUrl')
        if external_url:
            external_url = external_url.string
        else:
            external_url = url
    except:
        external_url = url

    return external_url, description


def get_external_url(url):
    try:
        return asyncio.run(parse_url(url))
    except RuntimeError:
        # If event loop already running
        loop = asyncio.get_event_loop()
        task = loop.create_task(parse_url(url))
        return loop.run_until_complete(task)


def extract_job_details(html):
    try:
        soup = BeautifulSoup(html, 'html.parser')
        job_listings = []
        
        # Debug counter for tracking matching results
        total_jobs = 0
        matching_jobs = 0

        for job in soup.select('ul.jobs-search__results-list li'):
            total_jobs += 1
            job_data = {}

            title_tag = job.select_one('h3.base-search-card__title')
            job_data['title'] = title_tag.get_text(strip=True) if title_tag else None

            company_tag = job.select_one('h4.base-search-card__subtitle a')
            company_name = company_tag.get_text(strip=True) if company_tag else None
            job_data['company'] = company_name
            
            # Print for debugging
            clean_company_name = clean_name(company_name)
            company_match = is_company_in_list(company_name)
            
            # Skip jobs from companies not in our target list
            if not company_match:
                continue
                
            matching_jobs += 1

            location_tag = job.select_one('span.job-search-card__location')
            job_data['location'] = location_tag.get_text(strip=True) if location_tag else None

            time_tag = job.select_one('time')
            job_data['posted_time'] = time_tag.get_text(strip=True) if time_tag else None
            job_data['posted_datetime'] = time_tag.get('datetime') if time_tag else None

            link_tag = job.select_one('a.base-card__full-link')
            url = link_tag.get('href') if link_tag else None

            logo_tag = job.select_one(".artdeco-entity-image")
            job_data['logo_url'] = logo_tag.get('src') if logo_tag else None

            external_url, description  = get_external_url(url)
            job_data['job_url'] = external_url
            job_data['description'] = description
            job_listings.append(job_data)

        print(f"📊 Extraction stats: {matching_jobs}/{total_jobs} jobs matched target companies")
        return job_listings
    except Exception as e:
        error_message = f"Failed to extract job details: {str(e)}"
        notify_failure(error_message, "extract_job_details")
        return []

def check_page_content_updated(previous_job_count):
    """Check if page content has been updated after clicking 'See more'."""
    try:
        # Count current visible job cards
        current_job_cards = driver.find_elements(By.CSS_SELECTOR, 
            ".job-card-container--clickable, .jobs-search__results-list li")
        current_count = len(current_job_cards)
        
        # Check if we have more jobs than before
        if current_count > previous_job_count:
            print(f"✅ Page updated: {previous_job_count} → {current_count} jobs")
            return True, current_count
        else:
            print(f"❌ Page did not update: Still showing {current_count} jobs")
            return False, current_count
    except Exception as e:
        print(f"⚠️ Error checking page content: {e}")
        return False, previous_job_count

def insert_jobs_to_db(job_listings):
    """Delete all jobs with data_source='linkedin' and insert new jobs"""
    if not job_listings:
        logger.info("No jobs to insert into database")
        return 0
    
    inserted_count = 0
    
    try:
        session = Session()
        try:
            # Delete all existing records with data_source='linkedin'
            logger.info("Deleting all existing LinkedIn jobs from database...")
            deleted_count = session.query(Job).filter(Job.data_source == 'linkedin').delete()
            logger.info(f"Deleted {deleted_count} existing LinkedIn jobs")
            
            # Insert new records
            logger.info(f"Inserting {len(job_listings)} new jobs...")
            for job in job_listings:
                try:
                    # Skip jobs with certain keywords in salary if needed
                    salary = job.get('salary', '')
                    
                    # Create new job object
                    new_job = Job(
                        job_title=job.get('title', ''),
                        company_name=job.get('company', ''),
                        company_logo=job.get('logo_url', ''),
                        salary=job.get('salary', '') if job.get('salary') else None,
                        posted_date=job.get('posted_date', ''),
                        experience='Full-time',  # Default or extract from job data if available
                        location=job.get('location', '') if job.get('location') else None,
                        apply_link=job.get('job_url', ''),
                        description=job.get('description', ''),
                        data_source='linkedin'
                    )
                    
                    session.add(new_job)
                    inserted_count += 1
                except Exception as e:
                    logger.error(f"Error processing job object: {str(e)}")
            
            # Commit the changes
            session.commit()
            logger.info(f"Successfully inserted {inserted_count} new jobs")
            
        except Exception as e:
            error_message = f"Database operation error: {str(e)}"
            logger.error(error_message)
            notify_failure(error_message, "insert_jobs_to_db")
            session.rollback()
        finally:
            session.close()
    except Exception as e:
        error_message = f"Database session error: {str(e)}"
        logger.error(error_message)
        notify_failure(error_message, "insert_jobs_to_db")
    
    return inserted_count
	
def load_all_jobs():
    global chat_id  # Declare chat_id as global within this function
    
    try:
        jobs_found = 0
        consecutive_no_button_found = 0
        consecutive_page_not_updated = 0
        max_no_button_attempts = 3
        max_no_update_attempts = 2
        
        total_jobs = []
        processed_page_count = 0

        while True:
            close_popups()
            
            # Scroll smoothly in chunks for more human-like behavior
            scroll_height = driver.execute_script("return document.body.scrollHeight")
            current_position = driver.execute_script("return window.pageYOffset")
            step = random.randint(300, 700)
            
            while current_position < scroll_height:
                driver.execute_script(f"window.scrollTo(0, {current_position + step});")
                current_position += step
                human_delay(0.3, 0.7)
            
            # Count visible job cards to track progress
            job_cards = driver.find_elements(By.CSS_SELECTOR, 
                ".job-card-container--clickable, .jobs-search__results-list li")
            current_job_count = len(job_cards)
            
            page_source = driver.page_source
            jobs = extract_job_details(page_source)
            
            # Add new jobs to our list
            total_jobs.extend(jobs)
            processed_page_count += 1
            
            # Every few pages, deduplicate and save results
            if processed_page_count % 3 == 0:
                # Remove duplicates before saving
                total_jobs = remove_duplicates(total_jobs)
                
                with open("linkedin_filtered_jobs.json", "w", encoding="utf-8") as json_file:
                    json.dump(total_jobs, json_file, indent=2, ensure_ascii=False)
                print(f"💾 Saved {len(total_jobs)} unique jobs to JSON (after {processed_page_count} pages)")

            # Print progress only if we found more jobs
            if current_job_count > jobs_found:
                print(f"📊 Found {current_job_count} total jobs, {len(total_jobs)} matching companies")
                jobs_found = current_job_count
            
            # Try to find and click "See more" button
            button_found = False
            
            try:
                # Try different approaches to find the "See more" button
                see_more_selectors = [
                    "//button[contains(@class, 'infinite-scroller__show-more')]",
                    "//button[contains(text(), 'See more')]",
                    "//button[contains(text(), 'Show more')]",
                    ".infinite-scroller__show-more-button",
                    ".more-jobs-button"
                ]
                
                see_more_button = None
                for selector in see_more_selectors:
                    elements = []
                    try:
                        if selector.startswith("//"):
                            elements = driver.find_elements(By.XPATH, selector)
                        else:
                            elements = driver.find_elements(By.CSS_SELECTOR, selector)
                        
                        if elements and len(elements) > 0 and elements[0].is_displayed():
                            see_more_button = elements[0]
                            button_found = True
                            break
                    except:
                        continue
                
                if see_more_button:
                    # Make sure the button is in view and wait for it to be clickable
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", see_more_button)
                    human_delay(1, 2)
                    
                    # Remember job count before clicking
                    pre_click_job_count = current_job_count
                    
                    # Try JavaScript click which is more reliable
                    driver.execute_script("arguments[0].click();", see_more_button)
                    print("🔄 Loading more jobs...")
                    human_delay(3, 5)  # Give more time to load
                    
                    # Check if the page has actually been updated with new content
                    page_updated, new_job_count = check_page_content_updated(pre_click_job_count)
                    
                    if page_updated:
                        consecutive_no_button_found = 0  # Reset button counter on success
                        consecutive_page_not_updated = 0  # Reset update counter on success
                    else:
                        consecutive_page_not_updated += 1
                        print(f"⚠️ Page didn't update after clicking 'See more' ({consecutive_page_not_updated}/{max_no_update_attempts})")
                        
                        # If we've had too many consecutive non-updates, assume we're done
                        if consecutive_page_not_updated >= max_no_update_attempts:
                            print("⛔ Too many failed page updates. Breaking loop.")
                            break
                else:
                    consecutive_no_button_found += 1
                    print(f"⚠️ No 'See more' button found (attempt {consecutive_no_button_found}/{max_no_button_attempts})")
                    human_delay(2, 3)  # Wait a bit and try again with a fresh scroll
                    
            except (ElementClickInterceptedException, StaleElementReferenceException) as e:
                print(f"⚠️ Click attempt failed: {e}")
                consecutive_no_button_found += 1
                human_delay(1, 2)

            except Exception as e:
                print(f"⚠️ Error during loading: {e}")
                consecutive_no_button_found += 1
                human_delay(1, 2)
            
            # If we haven't found the button for several consecutive attempts, assume we've reached the end
            if consecutive_no_button_found >= max_no_button_attempts:
                print(f"✅ No more 'See more' buttons found after {max_no_button_attempts} attempts. Done loading jobs.")
                break
        
        # Final job count
        final_count = len(driver.find_elements(By.CSS_SELECTOR, 
            ".job-card-container--clickable, .jobs-search__results-list li"))
        
        # Final deduplication
        total_jobs = remove_duplicates(total_jobs)

        insert_jobs_to_db(total_jobs)
        
        print(f"🏁 Total jobs loaded: {final_count}")
        print(f"🏁 Filtered jobs (matching companies): {len(total_jobs)}")

        # Send success notification
        if chat_id is None:
            chat_id = get_chat_id(TOKEN)
        if chat_id:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"✅ LINKEDIN SCRAPER SUCCESS at {timestamp}\nTotal jobs loaded: {final_count}\nMatching companies: {len(total_jobs)}"
            send_message(TOKEN, message, chat_id)

        return len(total_jobs)
    except Exception as e:
        error_message = f"Error in load_all_jobs: {str(e)}"
        notify_failure(error_message, "load_all_jobs")
        return 0

# Optional - Function to deduplicate an existing JSON file
def deduplicate_existing_json(filepath="linkedin_filtered_jobs.json"):
    """Removes duplicates from an existing JSON file."""
    global chat_id  # Declare chat_id as global within this function
    
    try:
        print(f"Deduplicating existing file: {filepath}")
        
        # Load existing data
        with open(filepath, 'r', encoding='utf-8') as file:
            existing_jobs = json.load(file)
            
        original_count = len(existing_jobs)
        print(f"Original job count: {original_count}")
        
        # Remove duplicates
        unique_jobs = remove_duplicates(existing_jobs)
        
        # Save deduped data
        with open(filepath, 'w', encoding='utf-8') as file:
            json.dump(unique_jobs, file, indent=2, ensure_ascii=False)
            
        print(f"Deduplication complete: {original_count} → {len(unique_jobs)} jobs")
        return len(unique_jobs)
    except Exception as e:
        error_message = f"Error in deduplicate_existing_json: {str(e)}"
        print(error_message)
        notify_failure(error_message, "deduplicate_existing_json")
        return 0

try:
    # Initialize chat_id early to avoid scope issues
    if chat_id is None:
        chat_id = get_chat_id(TOKEN)

    setup_database()
    
    load_all_jobs()
except Exception as e:
    error_message = f"Critical failure in main execution: {str(e)}"
    notify_failure(error_message, "main_execution")
finally:
    # Always close the driver
    try:
        driver.quit()
        print("Browser closed")
    except:
        pass