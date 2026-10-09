"""A 12-page printer manual used as the evaluation document. Each fact the
questions ask about lives on one known page (see dataset.json)."""

PAGES = [
    # 1
    """ACME X200 LASER PRINTER - USER MANUAL
Model X200, firmware 3.2. Please read this manual before using the printer.

Contents
Safety information ........................ page 2
Unpacking and setup ....................... page 3
Connecting to a computer or network ....... page 4
Loading paper ............................. page 5
Printing .................................. page 6
Control panel and settings ................ page 7
Error codes ............................... page 8
Troubleshooting ........................... page 9
Maintenance and supplies .................. page 10
Specifications ............................ page 11
Warranty and support ...................... page 12""",
    # 2
    """SAFETY INFORMATION
Place the printer on a flat, stable surface that can hold its weight.
Leave at least 10 cm of free space around the printer for ventilation. Do not operate it inside
a closed cabinet or drawer: the fan needs fresh air and the printer may overheat.
Use only the power cord supplied in the box and connect it to a grounded outlet.
The X200 is a Class 1 laser product. Never open the laser unit or look into the beam.
Keep toner out of reach of children. If toner gets on your skin, wash it off with cold water;
hot water sets the toner. Do not burn used cartridges.
The fuser area becomes very hot during printing. Wait before touching parts inside.""",
    # 3
    """UNPACKING AND SETUP
What is in the box: the X200 printer, a starter toner cartridge (TN-450, about 1,000 pages),
a pre-installed drum unit (DR-450), a power cord, a USB cable, and this manual.
1. Remove all orange shipping tape and the cardboard insert from the paper tray.
2. Open the front cover and pull out the drum unit.
3. Shake the toner cartridge gently from side to side and push it into the drum unit until it
   clicks into place.
4. Slide the drum unit back into the printer and close the front cover.
5. Connect the power cord and press the power button. The printer warms up in about 15 seconds.""",
    # 4
    """CONNECTING TO A COMPUTER OR NETWORK
USB: connect the supplied USB cable and install the driver from acme.example/drivers.
Ethernet: plug a network cable into the LAN port on the back of the printer.
Wi-Fi with WPS: press the WPS button on your router, then within two minutes hold the Wi-Fi
button on the printer for 3 seconds. The Wi-Fi light stops blinking when it is connected.
Wi-Fi without WPS: on the control panel choose Network > Wireless Setup Wizard, select your
network name and type the password.
Printing from a phone: install the free Acme Print app (Android and iOS). If there is no router,
use Wi-Fi Direct: choose Network > Wi-Fi Direct on the printer and connect your phone to it.""",
    # 5
    """LOADING PAPER
The paper tray holds up to 250 sheets of 80 gsm plain paper. Do not load above the max mark.
Supported sizes: A4, Letter, Legal, A5 and B5. Supported paper weight: 60 to 163 gsm.
Fan the stack of paper before loading it to prevent sheets from sticking together.
Adjust the paper guides so that they touch the edges of the stack.
Envelopes, labels and thick card stock go in the manual feed slot at the front, one sheet at a
time, with the printing side facing up. Supported envelopes: DL, C5 and Com-10.""",
    # 6
    """PRINTING
Print speed: up to 34 pages per minute (A4) and 36 pages per minute (Letter).
The first page comes out in under 8.5 seconds from Ready mode.
Automatic two-sided printing: the X200 prints on both sides of the paper without turning pages
by hand. In the print dialog choose Two-sided (duplex) printing.
Print quality: choose Normal (600 dpi) or Fine (1200 dpi) in the driver.
Toner Save mode uses less toner for drafts: turn it on in Settings > Print > Toner Save.
Printed pages will look lighter in Toner Save mode.""",
    # 7
    """CONTROL PANEL AND SETTINGS
The control panel has a 2-line display, the OK and Cancel buttons, and the arrow keys.
Sleep mode starts after 1 minute without activity by default. You can change the delay from
1 to 60 minutes in Settings > General > Sleep Time. Deep Sleep starts after 4 hours.
Web settings: type the printer's IP address into a browser to open its web page.
The default admin password for the web page is printed on the back label as "Pwd".
Change it after the first login.
Factory reset: hold Cancel and OK together for 10 seconds while the printer is on. All network
and user settings are erased.""",
    # 8
    """ERROR CODES
E01  No paper. Load paper into the tray.
E02  Paper jam in the tray. Remove the tray and pull out the stuck paper.
E03  Paper jam inside the printer. See Troubleshooting on page 9.
E04  Front cover open. Close the front cover firmly.
E05  Toner low. Order a new cartridge soon.
E06  Toner empty. Replace the toner cartridge.
E07  Toner cartridge not detected. Remove it and push it back into the drum unit until it clicks.
E08  Drum near end of life. Order a new drum unit.
E09  Replace the drum unit.
E10  Memory full. Reduce the resolution or split the print job.
E11  Wi-Fi connection failed. Check the network password and signal.
E12  Fuser overheating. Turn off the printer and wait 30 minutes before turning it on again.""",
    # 9
    """TROUBLESHOOTING
Paper stuck inside the printer: turn the printer off, open the front cover and pull out the
drum unit. Gently pull the sheet out with both hands. If you cannot reach it, open the rear
cover and pull the paper out from the back. Check the duplex tray underneath as well.
Faded or light prints: Toner Save mode may be on, or the toner is running low. Take the
cartridge out and shake it gently from side to side to spread the remaining toner.
Vertical streaks: clean the corona wire (see Maintenance).
The printer keeps losing the wireless connection: move it closer to the router and make sure
Sleep mode is not set to turn off Wi-Fi.""",
    # 10
    """MAINTENANCE AND SUPPLIES
Toner: the standard cartridge TN-450 prints about 3,000 pages. The high-yield cartridge
TN-450XL prints about 6,000 pages. Pages are measured at 5% coverage.
Drum unit: the DR-450 drum lasts about 12,000 pages. Replace it when E09 is shown.
Cleaning the corona wire: take out the drum unit and slide the green tab from left to right
several times, then return it to the home position. Do this every time you change the toner.
Clean the outside of the printer with a soft, dry cloth. Do not use alcohol or sprays.
Use genuine Acme supplies. Damage caused by third-party toner is not covered.""",
    # 11
    """SPECIFICATIONS
Dimensions (W x D x H): 356 x 360 x 183 mm. Weight: 7.2 kg without toner.
Memory: 128 MB. Processor: 800 MHz.
Power: 220 to 240 V AC, 50/60 Hz. Power use: 450 W printing, 45 W ready, 0.8 W deep sleep.
Noise level: 49 dB while printing, 30 dB in ready mode.
Maximum monthly duty cycle: 15,000 pages. Recommended monthly print volume: 250 to 2,000 pages.
Operating temperature: 10 to 32 C. Humidity: 20 to 80% without condensation.""",
    # 12
    """WARRANTY AND SUPPORT
The X200 comes with a limited warranty of 2 years from the date of purchase.
Register the printer within 30 days of purchase at acme.example/register to extend the warranty
to 3 years at no cost.
The warranty covers manufacturing defects in parts and labour. It does not cover damage
from misuse, power surges, or third-party toner and drum units.
Customer support: call 1800-123-4567 (toll free), Monday to Saturday, 9 am to 6 pm,
or email support@acme.example. Keep your invoice as proof of purchase.""",
]
