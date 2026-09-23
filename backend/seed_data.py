"""Vocabularies and text fragments for the seeded dataset.

Every list here is drawn from what the live Lead Call API actually returns
(measured 2026-09-22/23 across 300 call-active leads and 78 calls), with the
weights reflecting the observed frequencies. Keeping them in one module makes
the ratios auditable and keeps seed.py about structure rather than content.

The values are real CRM *vocabulary*; none of the records built from them are
real leads or real calls.
"""

# --------------------------------------------------------------------------
# Lead vocabulary -- (value, weight) drawn from live frequency counts
# --------------------------------------------------------------------------

# Stages, grouped by the outcome they imply. seed.py picks the group first so a
# lead's stage and its analysis outcome always agree.
STAGES_FOLLOW_UP = ["Follow-up 1", "Follow-up 2", "Likely to Enroll", "Qualified",
                    "Payment Link Shared", "Enroll Later", "Exploring Courses"]
STAGES_DROPPED = ["Not Interested", "Junk", "Invalid", "Not Looking for the Course",
                  "Did Not Apply", "Not Eligible", "Not Aware of the Course"]
STAGES_CONTACTED = ["DNP 1", "DNP 2", "DNP 3", "DNP 4", "DNP 5", "Not Responding 1",
                    "Not Responding 2", "Not Reachable", "Language Barrier", "Reassigned"]
STAGES_CONVERTED = ["Converted"]
STAGES_NEW = ["New"]

PRODUCTS = [
    ("Business Analytics with DM", 67), ("iit-data-science", 61),
    ("IIT-Machine-Learning-Program", 58), ("DevOps-Program", 26),
    ("Full Stack Development", 23), ("common", 18), ("IIMI-PM", 18),
    ("Mech-Cad-Course", 8), ("Intel AI-ML", 7), (None, 6),
    ("UI-UX-Course", 5), ("SDE with AI", 2),
    ("CAD with Automotive & Product Design", 1),
]

# `common`, `do-not-know` and `career_consultation` are sentinels the CRM uses
# for "no product recorded" -- they are not courses and must not be offered as
# though they were.
SENTINEL_PRODUCTS = {"common", "do-not-know", "career_consultation"}

LEAD_SOURCES = [
    ("Facebook", 231), ("YouTube-Influencer", 13), ("Google-Search", 13),
    ("Inbound Phone call", 11), ("GUVI-App", 6), ("Not Set", 5),
    ("Onsite-Events", 4), ("Benefit-Box", 3), ("GUVI-Social-Media", 2),
    ("LinkedIn-Ad", 2), ("IIM-Indore", 2), ("Blog", 2), ("Sales-Team", 1),
]

LANGUAGES = [("Tamil", 92), ("English", 88), ("Hindi", 63), ("Telugu", 22),
             (None, 16), ("Others", 9), ("Malayalam", 5), ("Kannada", 5)]

STATES = [
    (None, 186), ("Tamil Nadu", 43), ("Maharashtra", 21), ("Uttar Pradesh", 11),
    ("Karnataka", 8), ("National Capital Territory of Delhi", 5),
    ("Andhra Pradesh", 5), ("Telangana", 4), ("Gujarat", 3), ("West Bengal", 3),
    ("Punjab", 2), ("Odisha", 2), ("Assam", 1), ("Bihar", 1),
]

CITIES = [(None, 80), ("Chennai", 8), ("Coimbatore", 4), ("Madurai", 3), ("Bengaluru", 5),
          ("Hyderabad", 4), ("Mumbai", 4), ("Pune", 3), ("Delhi", 3), ("Kolkata", 2)]

SEGMENTATIONS = [(None, 186), ("M1", 101), ("M2", 12), ("M3", 1)]

SOURCE_CAMPAIGNS = [(None, 60), ("Not Set", 20), ("DataScience-Retargeting", 8),
                    ("AIML-Lookalike", 6), ("FullStack-Prospecting", 5), ("Nurturing", 4)]
SOURCE_MEDIUMS = [(None, 55), ("Not Set", 20), ("paid-social", 12), ("cpc", 8),
                  ("Whatsapp", 6), ("Email", 4), ("organic", 3)]
SOURCE_CONTENTS = [(None, 70), ("Not Set", 12), ("video-testimonial", 6),
                   ("carousel-placement", 5), ("lead-form-v3", 4)]

# Nurturing is a tracking URL on the ~1% of leads that have one.
NURTURING_URLS = [
    "https://www.guvi.in/mlp/demo-session?utm_source=Organic-Marketing&utm_medium=Whatsapp"
    "&utm_campaign=Nurturing&utm_content=North_Leads_VC_Demo",
    "https://www.guvi.in/zen-class/data-science-course/?utm_source=Organic-Marketing"
    "&utm_medium=Whatsapp&utm_campaign=Nurturing&utm_content=DataScience_lsq_automation",
    "https://www.guvi.in/zen-class/devops-course/?utm_source=Organic-Marketing"
    "&utm_medium=Whatsapp&utm_campaign=Nurturing&utm_content=Devops_lsq_automation",
]

DISPOSITIONS = ["Connected", "Not Connected"]

# --------------------------------------------------------------------------
# BDAs -- CRM-style full names, but a deliberately safe mailbox (see below)
# --------------------------------------------------------------------------

# Every seeded BDA is addressed to ONE real mailbox on purpose.
#
# The reminder digest emails a BD at their own address. The CRM's real owners
# are live @hclguvi.com mailboxes, so seeding those would point a working SMTP
# scheduler at actual colleagues. Names stay realistic; the address does not.
#
# Change this and you are choosing to email whoever it names.
SEED_CALLER_EMAIL = "premkumarp@guvi.in"

CALLER_NAMES = [
    "Bhavani A", "Abhishek Doyla", "Subash E", "Rahul Raj",
    "Prasanth S", "Lavakumar P", "Fazil J", "Praveen Kumar Subramanian",
    "Chandra Mouli CH", "Andrew Anderson", "Sujal Singh", "Shahrukh Ansari",
]

TEAMS = ["Inside Sales - South", "Inside Sales - North", "Enterprise", "Nurture Desk"]
ROLES = ["Sales", "Senior Sales Executive", "Sales Team Lead"]

# --------------------------------------------------------------------------
# Transcripts -- the real shape: [MM:SS] Agent:/Customer:, romanized Indic + English
# --------------------------------------------------------------------------

# Each entry: (language_label, [(speaker, line), ...]) for the body of a call.
# Openers and closers are combined with a middle section in seed.py.

OPENERS_EN = [
    [("Customer", "Hello."),
     ("Agent", "Yeah, hi, am I speaking to the right person?"),
     ("Customer", "Yes, speaking."),
     ("Agent", "This is a call from HCL GUVI. I just received your enquiry form."),
     ("Customer", "Okay, okay."),
     ("Agent", "Are you looking for any IT courses right now?")],
    [("Agent", "Hello, good morning. Am I speaking with you regarding the GUVI enquiry?"),
     ("Customer", "Yes, tell me."),
     ("Agent", "I am calling from HCL GUVI, the IIT-M research park incubated platform."),
     ("Customer", "Ah okay, I remember filling the form.")],
]

OPENERS_TA = [
    [("Agent", "ok, kelkaam."),
     ("Customer", "hello?"),
     ("Agent", "aa, yes, naan HCL GUVI-la irundhu call pannuren."),
     ("Customer", "aa, yes saar."),
     ("Agent", "namma GUVI platform-la neenga course-kkaaga enquiry pannirundheengale.")],
    [("Customer", "Halo."),
     ("Agent", "Halo? ya, hai, naan GUVI IIT research process-la irundhu call pannirukken."),
     ("Customer", "ok."),
     ("Agent", "enna course-kku neenga paakireenga-nnu konjam sollunga.")],
]

MIDDLES_EN = [
    [("Customer", "I am looking for a data analyst course, not a general IT course."),
     ("Agent", "Sure, we have a dedicated track for that with placement support."),
     ("Agent", "It is a four month program, weekend batches are also available."),
     ("Customer", "What about the fees? That is my main concern."),
     ("Agent", "I will share the full fee structure and the EMI options on WhatsApp.")],
    [("Customer", "I already tried one institute and the placement did not happen."),
     ("Agent", "I understand. We work with 3000 plus hiring partners."),
     ("Customer", "Can you show me the official placement numbers?"),
     ("Agent", "Yes, I will send the ATS report and the brochure.")],
]

MIDDLES_TA = [
    [("Customer", "ippo vendaam, naan paarkala."),
     ("Agent", "aama, onnum problem illai. konjam detail sollattaa?"),
     ("Agent", "namma course four months, placement support full-a irukku."),
     ("Customer", "saar, actually idhu placement eppadinna, ippo mark interview kuduppaanga?"),
     ("Agent", "aama, 3000+ hiring partners irukkaanga, naan details anuppuren.")],
    [("Customer", "enakku konjam yosikkanum, veetla kekkanum."),
     ("Agent", "kandippa, neenga pesitu sollunga."),
     ("Customer", "fees evlo aagum-nu mattum sollunga."),
     ("Agent", "naan WhatsApp-la full fee structure-um EMI option-um anuppuren.")],
]

# Closers carry the follow-up commitment. The key is included so seed.py can
# keep the transcript, the CRM's prose and our extracted datetime consistent.
CLOSERS = {
    "tomorrow_11": [("Customer", "Okay, set up a Google Meet tomorrow at 11 AM."),
                    ("Agent", "Done, I will send the invite and the details on WhatsApp.")],
    "tomorrow_12": [("Customer", "Schedule a call tomorrow at 12 pm, I will be free then."),
                    ("Agent", "Sure, I will call you at 12 and send the brochure before that.")],
    "tomorrow_10": [("Customer", "naalaikku kaalaila 10 manikku call pannunga."),
                    ("Agent", "sari saar, naalaikku 10 manikku call pannuren.")],
    "evening_8": [("Customer", "Send the details, and call me back at 8 PM today."),
                  ("Agent", "Noted, I will call you at 8 in the evening.")],
    "next_week": [("Customer", "I am travelling this week, call me next week."),
                  ("Agent", "Sure, I will reach out next week with the details.")],
    "no_date": [("Customer", "Send everything on WhatsApp, I will review and get back."),
                ("Agent", "Sure, I am sharing it now. Please do go through it.")],
    "no_date_ta": [("Customer", "WhatsApp-la anuppunga, naan paathutu sollren."),
                   ("Agent", "sari saar, ippo anuppuren.")],
    "converted": [("Customer", "I have made the payment just now, please confirm."),
                  ("Agent", "Received, thank you. Your batch starts on Monday.")],
    "dropped": [("Customer", "I am not interested, I already joined somewhere else."),
                ("Agent", "Understood, thank you for letting me know.")],
    "dropped_ta": [("Customer", "enakku interest illa, naan vera place-la join panniten."),
                   ("Agent", "sari saar, thank you.")],
}

# --------------------------------------------------------------------------
# analysisSummary -- the CRM's own analysis
# --------------------------------------------------------------------------

# All ten questions appear on every record the CRM produces, in this order.
AUTOFILL_QUESTIONS = [
    "What city / region is the prospect from?",
    "Were institutional credentials and certifications highlighted?",
    "What payment or enrollment option was discussed?",
    "Was HCL GUVI's vernacular learning advantage explained?",
    "What program or course did the prospect express interest in?",
    "What is the prospect's current status?",
    "Did the prospect show immediate intent to enroll?",
    "What is the prospect's preferred language of instruction?",
    "What is the prospect's highest qualification?",
    "What follow-up action was locked in?",
]

# The follow-up answer is prose, never a timestamp -- that gap is the whole
# reason this system exists. Keyed to match the transcript closer.
FOLLOW_UP_PROSE = {
    "tomorrow_11": "Google Meet tomorrow at 11 AM and WhatsApp details",
    "tomorrow_12": "Schedule Google Meet tomorrow at 12 pm and send brochure on WhatsApp",
    "tomorrow_10": "Call back tomorrow 10-11 AM with WhatsApp message",
    "evening_8": "Send resume via WhatsApp and callback at 8 PM",
    "next_week": "Follow-up call next week after travel",
    "no_date": "Share details on WhatsApp and call back",
    "no_date_ta": "Send course details on WhatsApp",
    "converted": "n/a",
    "dropped": "n/a",
    "dropped_ta": "n/a",
}

PROSPECT_STATUSES = ["Job seeker with career gap", "Final year student", "Working professional",
                     "Fresher looking for placement", "Career switcher from non-IT"]
QUALIFICATIONS = ["B.E / B.Tech", "B.Sc", "MCA", "B.Com", "n/a", "M.Tech"]
PAYMENT_OPTIONS = ["EMI discussed, no-cost 12 months", "Full payment with scholarship",
                   "n/a", "Payment link shared", "EMI options to be shared on WhatsApp"]

VIOLATIONS = [
    ("CRITICAL", "False Placement Guarantee",
     "Agent stated placement is guaranteed, which overstates the programme's commitment."),
    ("CRITICAL", "Fee / Refund Misrepresentation",
     "Agent described the fee as fully refundable without stating the refund conditions."),
    ("HIGH", "Certification Misrepresentation",
     "Agent implied the certificate is issued directly by IIT-M rather than co-branded."),
    ("HIGH", "Overpromising Batch Start Date",
     "Agent committed to a batch start date that is not confirmed in the schedule."),
    ("HIGH", "False Urgency / Fake Scarcity",
     "Agent claimed only two seats remain in order to pressure an immediate decision."),
    ("MEDIUM", "Incomplete EMI Disclosure",
     "EMI was presented without mentioning the processing fee or the tenure options."),
    ("MEDIUM", "Overpromising Batch Start Date",
     "Agent suggested the batch could be advanced on request without confirming."),
    ("LOW", "Missed Greeting / Introduction",
     "Agent skipped a proper greeting and did not introduce themselves or HCL GUVI."),
    ("LOW", "Unprofessional Language",
     "Agent used casual filler language inconsistent with the call script."),
]

IMPROVEMENT_TIPS = [
    "Start the call with a proper introduction including name and HCL GUVI, and clearly state the purpose of the call.",
    "Proactively discuss course fees, EMI options, and scholarships to address financial concerns early.",
    "Highlight HCL GUVI's vernacular learning advantage and IIT-M/Intel certifications to build credibility.",
    "Confirm the prospect's preferred language at the start and switch if it improves comprehension.",
    "Summarise the agreed next step out loud before ending the call so both sides agree on it.",
    "Avoid absolute placement claims; describe the placement support process instead.",
]

SUMMARY_TEMPLATES = [
    ("Salesperson called the prospect, {status}, to pitch HCL GUVI's {product} program. "
     "Customer raised concerns about placement outcomes and asked for official details. "
     "Salesperson highlighted hiring partners, programme duration and salary outcomes. "
     "{next_step}"),
    ("Outbound call regarding the {product} programme. The prospect, {status}, asked about "
     "fees and batch timings. Salesperson explained the structure and the EMI options. "
     "{next_step}"),
    ("Salesperson followed up on a web enquiry for {product}. Customer, {status}, was "
     "comparing options and wanted the placement record in writing. {next_step}"),
]
