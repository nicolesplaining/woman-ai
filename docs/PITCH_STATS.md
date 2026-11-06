# Pitch Stats: Women's Prosthetic Hand CAD Automation

Last synced Nov 6, 2026 from the [live doc](https://claude.ai/artifact/5D5VmKtJbAkAjDEHgAXpCo). Edit the live doc, then re-export.

## Headline numbers

Each row pairs an e-NABLE pain point with the number our demo has to prove. Fill the "Ours" column as we test.

| Pain point | e-NABLE today | Ours | Status |
| --- | --- | --- | --- |
| Time to turn measurements into a final design file | 6–20 volunteer hours per device | \_\_\_ minutes | To measure |
| Fit accuracy | 2–3 mm of error makes a big difference to fit | ±\_\_\_ mm vs. measured hand | To measure |
| Cost of a bad fit | \~1 full day to 3D print; a misfit means a full reprint | \_\_\_% fit on first print | To measure |
| Bad input photos | Bad photo uploads are a recurring issue | \_\_\_% of bad photos caught before design starts | To measure |
| Volunteer hours freed per 100 devices | 600–2,000 hours (6–20 h × 100) | \_\_\_ hours | To measure |

## e-NABLE baseline

These are the problems e-NABLE describes today; each one becomes a before/after beat in the video.

- **Design is slow and manual.** A volunteer spends 6–20 hours per arm taking measurements and turning them into a final design file.
- **Fit is unforgiving.** A 2–3 mm difference in the design makes a big difference in fit.
- **Mistakes are expensive.** One print takes about a full day, so a misfit costs another day plus material.
- **Inputs are unreliable.** People often upload photos that are too poor to size from.

Sizing today relies on a knuckle-width scale factor ([e-NABLE sizing guide](https://enablingthefuture.org/resources-2/e-nable-device-sizing/)), and e-NABLE itself warns that measurement-only scaling "may not produce a properly fitting device."

## Why women

Women are fitted less often and are barely represented in the data prosthetics are designed from.

| Stat | Number | Source |
| --- | --- | --- |
| Successfully fitted with a prosthesis at discharge | Women 42.9% vs. men 68.6% (n=105) | [Gender differences in amputation outcome](https://pubmed.ncbi.nlm.nih.gov/17852206/) |
| Women among participants in prosthetic studies (veterans) | Historically under 5% | [Kuo et al., PM&R 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11849583/) |
| Women in the open residual-limb shape model OpenLimbTT | 4 of 33 scans (12%) | [OpenLimbTT](https://github.com/abel-research/OpenLimbTT) |
| Women in the main public hand-shape model MANO | Not reported (31 subjects total) | [MANO](https://mano.is.tue.mpg.de/) |
| Public 3D scan datasets of partial-hand limbs | None found | Our search, Nov 2026 |
| Women in ANSUR II, the dataset we size from | 1,986 of 6,068 | [ANSUR II](https://www.openlab.psu.edu/ansur2/) |

Pitch line: the evidence on women amputees is fragmented and low quality ([2024 systematic review](https://www.tandfonline.com/doi/pdf/10.1080/19498241.2024.2426959)), so we design from women's hand data directly.

## Male vs. female hands

Women's hands are not scaled-down men's hands. They are about 6% shorter but about 11% narrower, so one scale factor cannot fit both length and width.

| Measure (ANSUR II means) | Men (n=4,082) | Women (n=1,986) | Women vs. men |
| --- | --- | --- | --- |
| Hand length | 193.3 mm | 181.1 mm | 6.3% shorter |
| Hand breadth (knuckles) | 88.3 mm | 78.2 mm | 11.4% narrower |
| Breadth ÷ length | 0.457 | 0.432 | Relatively narrower, longer fingers |

The research agrees: hand breadth is the strongest sex difference, while length differs less ([2024 study](https://www.sciencedirect.com/science/article/pii/S2665910724000239), [Turkish sample, n=385](https://pubmed.ncbi.nlm.nih.gov/24951406/), [geometric morphometrics](https://www.cambridge.org/core/journals/twin-research-and-human-genetics/article/geometric-morphometric-assessment-of-hand-shape-and-comparison-to-the-2d4d-digit-ratio-as-a-marker-of-sexual-dimorphism/BAD1ABEC7C06C139F2A2F2D97AABABF8)).

**The inequity, in numbers.** e-NABLE devices are one base model, scaled uniformly from knuckle width. Suppose that base has average male proportions; e-NABLE does not document whose hand it came from. Then for an average woman:

- **Scaled to her knuckle width:** the hand comes out about 10 mm (5.5%) shorter than hers.
- **Scaled to her hand length:** it comes out about 4.5 mm too wide, already past e-NABLE's 2–3 mm fit window.

Pitch line: "Every e-NABLE hand starts as one shape and gets stretched evenly. Women's hands differ in shape, not just size, so they absorb the misfit." Our fix scales width and length separately, using women's data from ANSUR II.

The ANSUR means above are from a [secondary summary](https://bodytype-calculator.com/average-hand-size/) of the [public ANSUR II data](https://www.openlab.psu.edu/ansur2/), and the misfit figures are our arithmetic on them. Recompute both from the ANSUR II CSVs before the video.

## Stat bank

Pull from here for the video. Lead with the peer-reviewed and official stats; check the "verify" ones before using them on screen.

| Theme | Stat | Source | Strength |
| --- | --- | --- | --- |
| Women | Women were successfully fitted with a prosthesis less often at discharge: 42.9% vs. 68.6% for men (n=105) | [Gender differences in amputation outcome](https://pubmed.ncbi.nlm.nih.gov/17852206/) | Peer-reviewed |
| Women | Women have historically made up under 5% of participants in prosthetic studies (veterans) | [Kuo et al., PM&R 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11849583/) | Peer-reviewed |
| Women | Women are about 31% of amputees | [Healthline](https://www.healthline.com/health/amputation) | Secondary, verify |
| Women | Only 4 of 33 limb scans in the open shape model OpenLimbTT are from women | [OpenLimbTT](https://github.com/abel-research/OpenLimbTT) | Official org |
| Scale | Nearly 2.3 million people in the US live with limb loss; 5.6 million including limb difference | [Amputee Coalition, 2024](https://amputee-coalition.org/5-6-million-americans-living-with-limb-loss-limb-difference/) | Official org |
| Scale | Congenital upper limb differences affect about 20–30 per 10,000 live births | [J Hand Surg review, 2026](https://pubmed.ncbi.nlm.nih.gov/41689580/) | Peer-reviewed |
| Access | Only 1 in 10 people who need assistive products, including prostheses, have access to them | [WHO](https://www.who.int/teams/health-product-policy-and-standards/assistive-and-medical-technology/assistive-technology/prosthetics-and-orthotics-services) | Official org |
| Access | 2.5 billion people need assistive products, rising to 3.5 billion by 2050 | [WHO/UNICEF, 2022](https://www.who.int/news/item/16-05-2022-almost-one-billion-children-and-adults-with-disabilities-and-older-persons-in-need-of-assistive-technology-denied-access--according-to-new-report) | Official org |
| Cost | A myoelectric (bionic) hand costs about $20,000–$100,000 | [Is Brave guide](https://isbrave.com/guides/myoelectric-prosthesis/) | Secondary, verify |
| Cost | Medicare pays $26,288–$35,051 for a powered hand (code L6880) | [GrabaRobot, citing the CMS fee schedule](https://www.grabarobot.com/blog/bionic-prosthetic-hand-price-guide-2026/) | Secondary, verify |
| Cost | An e-NABLE printed hand costs about $30–$50 in materials | [Engineering For Change](https://www.engineeringforchange.org/solutions/product/e-nable-3d-printing-prosthetic-devices/) | Official org |
| Abandonment | Adults reject upper limb prostheses at 26% (body-powered) and 23% (electric); children at 45% and 35% | [Biddiss & Chau, 2007](https://pubmed.ncbi.nlm.nih.gov/17979010/) | Peer-reviewed |
| Abandonment | 27.7% of 685 US upper limb loss respondents had abandoned a prosthesis | [Unmet need cohort study](https://pmc.ncbi.nlm.nih.gov/articles/PMC10411186/) | Peer-reviewed |
| e-NABLE | About 40,000 volunteers in 100+ countries have delivered devices to 10,000–15,000 recipients | [Enabling The Future](http://enablingthefuture.org/) | Official org |
| e-NABLE | 6–20 volunteer hours to design one device from measurements | e-NABLE (our conversation) | Secondary, verify |

## Accuracy benchmarks

The target is under 2 mm mean error per hand dimension. That is inside e-NABLE's 2–3 mm fit window and beats the best published photo-based result (3.80 mm).

| Benchmark | Number to compare against | What it measures | Source |
| --- | --- | --- | --- |
| ISO 20685-1 (2018) | ≤ 1 mm mean difference for hand dimensions | Gold standard: scan-extracted vs. trained anthropometrist | [ISO 20685-1](https://www.iso.org/standard/63260.html) |
| e-NABLE fit window | 2–3 mm changes fit | Our product requirement | e-NABLE |
| HandAnthro (2026 preprint) | 3.80 mm mean error overall; 2.48 mm fingers; 6.17 mm palm/wrist; 97.8% usable captures | Single smartphone photo on letter paper vs. calipers, 44 dimensions | [arXiv 2609.37855](https://arxiv.org/abs/2609.37855) |
| FreiHAND (3D hand mesh) | 5.3 mm PA-MPVPE (Hamba, NeurIPS 2024) | 3D hand shape from one image; alignment removes true scale | [FreiHAND](https://lmb.informatik.uni-freiburg.de/projects/freihand/), [Hamba](https://openreview.net/forum?id=pCJ0l1JVUX) |
| ANSUR II observer error | Human measurer noise floor (value to look up) | How much two trained measurers disagree | [ANSUR II methods report](https://apps.dtic.mil/sti/pdfs/ADA611869.pdf) |
| e-NABLE AI HandSizer (in testing) | ~1.2 cm (12 mm) average error, designer's own tests; Phoenix v3 only. Not independently validated: use as a secondary point, lead with HandAnthro's 3.80 mm | Print scale from one photo of the sound hand next to a card; e-NABLE's current photo tool | [e-NABLE Device Sizing wiki](https://hub.e-nable.org/s/e-nable-devices/wiki/page/view?title=e-NABLE+Device+Sizing) |

**Our test plan**

1. Recruit volunteers, at least half women. Caliper-measure knuckle width, palm length and wrist circumference.
2. Have each volunteer take photos through our app, including some deliberately bad ones (blurry, angled, no reference object).
3. Report mean absolute error in mm per dimension, and the % of dimensions within 2 mm, split by sex.
4. Report the photo checker's catch rate on the bad photos. This fills the "bad photos" row above.
5. Print one test hand from our file and one from uniform e-NABLE scaling for the same woman. Film the fit side by side.

## To verify before the video

- [ ] Find a public e-NABLE page for the 6–20 hour, 2–3 mm and full-day print figures, or credit them to our conversation with e-NABLE.
- [ ] Confirm the ISO 20685-1 hand tolerance (1 mm) in the standard's text; the source excerpt was garbled.
- [ ] Note that HandAnthro is a preprint, not peer reviewed.
- [ ] Look up the ANSUR II hand breadth observer error in the Measurer's Handbook (Hotzman et al., 2011).
- [ ] Confirm e-NABLE has no women-specific base model. Searches found one base design per device, scaled from knuckle width.

## Sources

- [e-NABLE Device Sizing](https://enablingthefuture.org/resources-2/e-nable-device-sizing/)
- [Raptor Hand / Handomatic](https://enablingthefuture.org/upper-limb-prosthetics/the-raptor-hand/)
- [e-NABLE recommended hand designs](https://hub.e-nable.org/s/e-nable-devices/wiki/311/hand-designs-currently-recommended)
- [ISO 20685-1:2018](https://www.iso.org/standard/63260.html)
- [HandAnthro preprint](https://arxiv.org/abs/2609.37855)
- [FreiHAND](https://lmb.informatik.uni-freiburg.de/projects/freihand/)
- [Hamba, NeurIPS 2024](https://openreview.net/forum?id=pCJ0l1JVUX)
- [ANSUR II](https://www.openlab.psu.edu/ansur2/)
- [ANSUR II methods report (DTIC)](https://apps.dtic.mil/sti/pdfs/ADA611869.pdf)
- [Gender differences in amputation outcome](https://pubmed.ncbi.nlm.nih.gov/17852206/)
- [Kuo et al., PM&R 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11849583/)
- [Systematic review of female amputee literature, 2024](https://www.tandfonline.com/doi/pdf/10.1080/19498241.2024.2426959)
- [OpenLimbTT](https://github.com/abel-research/OpenLimbTT)
- [MANO](https://mano.is.tue.mpg.de/)
