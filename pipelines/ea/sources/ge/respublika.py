"""Georgia's single-member Parliament districts of 1992, 1995, and 1999, from the newspaper Sakartvelos Respublika.

The Central Election Commission's summary protocols of these elections were printed in the government newspaper
Sakartvelos Respublika, whose scanned issues the National Parliamentary Library of Georgia publishes in its Iverieli
digital library. The Commission's own electoral history volume (the `ge.cec_1990s` pipeline) cites these protocols
but prints only national totals, so the district contests come from the newspaper:

- 28 October 1992 (No. 222), page 1: the Commission's announcement of the 11 October 1992 results, naming the member
  elected in each of the 75 single-member districts that voted;
- 21 November 1995 (No. 171), page 3: the first round of 5 November 1995 in the 32 districts that elected a member
  outright, with each district's electorate and participants;
- 8 December 1995 (No. 183), page 1: the Commission's report of the repeat votes of 3 December 1995, naming the
  member elected in each of the six districts that voted again (the second round in districts 1 and 15, and the
  repeated voting at precincts of districts 3, 12, 36, and 51);
- 9 November 1999 (No. 304), page 5: the protocol of 7 November 1999 with the electorate and participants of every
  one of the 75 districts that voted on 31 October 1999, and the 49 members elected in the first round.

The scans have no text layer and no OCR engine reads Georgian print reliably, so the figures and names were read
from the page images by hand and are written out below with each row's place on the page. The 1999 district
figures add up exactly to the protocol's national electorate and participants, which the build checks. The
protocols print no candidate's votes, so each result holds the district's electorate and participants (where printed)
and the declared winner.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ge import cec_1990s

SOURCE = "ge.respublika"
GEORGIA = cec_1990s.GEORGIA
COUNTRY = cec_1990s.COUNTRY
SCHEME = "ge.district_1995"
AREA_SCHEME = "ge.rayon_1995"
LIBRARY = "https://dspace.nplg.gov.ge"
REGISTERED, VOTERS = 2, 14
POINTS = 72 / 300
"""Page images are scanned at 300 dpi; boxes are stored in PDF points."""


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC = key("source", "cec-respublika")


@dataclass(frozen=True)
class Issue:
    handle: int
    file: str
    page: int
    printed: str
    """The issue as the newspaper numbers and dates it."""

    @property
    def url(self) -> str:
        return f"{LIBRARY}/bitstream/1234/{self.handle}/1/{self.file}"

    @property
    def landing(self) -> str:
        return f"{LIBRARY}/handle/1234/{self.handle}"


ISSUE_1992 = Issue(10350, "SakartvelosRespublika_1992_N222.pdf", 1, "No. 222 (501), 28 October 1992")
ISSUE_1995 = Issue(125585, "Saqartvelos_Respublika_1995_N171.pdf", 3, "No. 171 (2087), 21 November 1995")
ISSUE_1999 = Issue(170809, "Saqartvelos_Respublika_1999_N304.pdf", 5, "No. 304 (3345), 9 November 1999")
ISSUE_1995_DECEMBER = Issue(125640, "Saqartvelos_Respublika_1995_N183.pdf", 1, "No. 183 (2099), 8 December 1995")
ISSUES = (ISSUE_1992, ISSUE_1995, ISSUE_1999)
ISSUE_1995_DECEMBER_COPY = (
    "https://web.archive.org/web/20240520010343id_/https://dspace.nplg.gov.ge/bitstream/1234/125640/1/"
    "Saqartvelos_Respublika_1995_N183.pdf"
)
"""The library's scan of No. 183 as the Wayback Machine captured it; the library itself refuses automated requests."""


# ---------------------------------------------------------------------------------------------------------------------
# What the pages print

# 1992: (multi-member district, single-member district, district as printed, member elected as printed).
WINNERS_1992 = (
    (1, 1, "მთაწმინდის", "კვარაცხელია ვალერი დავითის ძე"),
    (1, 2, "ვაკის", "ხუაჩიძე თენგიზ პავლეს ძე"),
    (1, 3, "საბურთალოს", "ორჯონიკიძე იზა არჩილის ასული"),
    (1, 4, "კრწანისის", "მუსელიანი ვახტანგ ალექსანდრეს ძე"),
    (1, 5, "ისნის", "გვერდწითელი მიხეილ პავლეს ძე"),
    (1, 6, "სამგორის", "არველაძე ტახირ ვლადიმერის ძე"),
    (2, 7, "ჩუღურეთის", "რჩეულიშვილი ვახტანგ შალვას ძე"),
    (2, 8, "დიდუბის", "ლეკიშვილი ნიკოლოზ მიხეილის ძე"),
    (2, 9, "ნაძალადევის", "იოსელიანი ჯაბა კონსტანტინეს ძე"),
    (2, 10, "გლდანის", "მჭედლიძე მიხეილ ლევანის ძე"),
    (3, 11, "საგარეჯოს", "გოდერძიშვილი გელა გიორგის ძე"),
    (3, 12, "გურჯაანის", "ჭეირანაშვილი ნიაზ ალექსანდრეს ძე"),
    (3, 13, "სიღნაღის", "დოლმაზაშვილი ავთანდილ რამანის ძე"),
    (3, 14, "დედოფლისწყაროს", "კობაიძე ალექსი ლევანის ძე"),
    (3, 15, "ლაგოდეხის", "კაკიაშვილი ნუგზარ ნოდარის ძე"),
    (3, 16, "ყვარლის", "მანძულაშვილი მიხეილ შალვას ძე"),
    (3, 17, "თელავის", "მეგუთნიშვილი სოსო არჩილის ძე"),
    (3, 18, "ახმეტის", "ახმეტელი გიორგი (ცეზარი) გიორგის ძე"),
    (3, 19, "თიანეთის", "მგელიაშვილი თეიმურაზ (ჯუბო) ივანეს ძე"),
    (4, 20, "რუსთავის", "შარტავა ჟიული კალისტრატეს ძე"),
    (4, 21, "გარდაბნის", "ჭელიძე ივერი რაჟდენის ძე"),
    (4, 22, "მარნეულის", "სიგუა თენგიზ იპოლიტეს ძე"),
    (4, 23, "ბოლნისის", "კიტოვანი თენგიზ კალისტრატეს ძე"),
    (4, 24, "დმანისის", "ციხელაშვილი ტარიელ ივანეს ძე"),
    (4, 25, "წალკის", "ჩიტლოვი დიმიტრი სამსონის ძე"),
    (4, 26, "თეთრიწყაროს", "წიკლაური მევლუდ შაქროს ძე"),
    (5, 27, "მცხეთის", "პატიაშვილი ჯუმბერ ილიას ძე"),
    (5, 28, "დუშეთის", "ნათელაშვილი შალვა ამბროსის ძე"),
    (5, 29, "ყაზბეგის", "გელაძე ვაჟა ნიკოლოზის ძე"),
    (5, 30, "კასპის", "ოთარაშვილი ვაჟა გიორგის ძე"),
    (5, 31, "ახალგორის", "ბალაშვილი ვალოდია ორდას ძე"),
    (5, 32, "გორის", "აბალაკი გივი გიორგის ძე"),
    (5, 33, "ქარელის", "ხუზალაძე ვასილ გიორგის ძე"),
    (5, 35, "ხაშურის", "ხვედელიძე გივი ანდროს ძე"),
    (5, 36, "ბორჯომის", "კურტანიძე ჯუმბერ გიორგის ძე"),
    (5, 37, "ახალციხის", "ბალიაშვილი ივანე (ვალიკო) იაკობის ძე"),
    (5, 38, "ადიგენის", "გელაძე ავთანდილ შოთას ძე"),
    (5, 39, "ასპინძის", "წითლანაძე გიორგი იაკობის ძე"),
    (5, 40, "ახალქალაქის", "ალექსანიანი ფერდინანდ არშენაკის ძე"),
    (5, 41, "ნინოწმინდის", "სახჩიანი ჟორა ზარზანდის ძე"),
    (6, 43, "ონის", "ლობჟანიძე გივი შალვას ძე"),
    (6, 44, "ამბროლაურის", "მახალდიანი სოსო თენგიზის ძე"),
    (6, 45, "ცაგერის", "ჩაკვეტაძე მურთაზ ახმედის ძე"),
    (6, 46, "ლენტეხის", "გაზდელიანი თენგიზ კორნელის ძე"),
    (6, 47, "მესტიის", "ცინდელიანი ლია შოთას ასული"),
    (6, 48, "ხარაგოულის", "ვეფხვაძე ნოდარ ვარლამის ძე"),
    (6, 49, "თერჯოლის", "ქაშაკაშვილი ამირან ნიკოლოზის ძე"),
    (6, 50, "საჩხერის", "ენუქიძე ზაზა მიხეილის ძე"),
    (6, 51, "ზესტაფონის", "გორგოძე ჯემალ ვალერიანის ძე"),
    (6, 52, "ბაღდათის", "გეწაძე გურამ იასონის ძე"),
    (6, 53, "ვანის", "ხურციძე ბეჟან ვლადიმერის ძე"),
    (6, 54, "სამტრედიის", "კოხრეიძე ნოდარ ლუკას ძე"),
    (6, 55, "ხონის", "ჩხენკელი მიხეილ მაქსიმეს ძე"),
    (6, 56, "ჭიათურის", "კიკნაძე გიორგი (გოგი) ვარლამის ძე"),
    (6, 57, "ტყიბულის", "ვარდოსანიძე ლევან გრიგოლის ძე"),
    (6, 58, "წყალტუბოს", "აბესაძე ინგუშა ზოტიკეს ძე"),
    (6, 59, "ქუთაისის", "შაშიაშვილი თეიმურაზ გივის ძე"),
    (7, 60, "ოზურგეთის", "ზერიძე კონსტანტინე თეიმურაზის ძე"),
    (7, 61, "ლანჩხუთის", "კილაძე ანზორ მილორდის ძე"),
    (7, 62, "ჩოხატაურის", "თავართქილაძე ანზორ ილარიონის ძე"),
    (8, 63, "აბაშის", "კალანდაძე თენგიზ გრიგოლის ძე"),
    (8, 64, "სენაკის", "კოხია ოთარ გრიგოლის ძე"),
    (8, 65, "მარტვილის", "კეკელია შალვა ნიკანდროს ძე"),
    (8, 66, "ხობის", "ნადარაია ნუგზარ ოთარის ძე"),
    (8, 70, "ფოთის", "მელია რომან დავითის ძე"),
    (9, 71, "სოხუმის", "ნადარეიშვილი თამაზ ვლადიმერის ძე"),
    (9, 75, "გულრიფშის", "წოწონავა ანზორ დავითის ძე"),
    (9, 76, "ოჩამჩირის", "ფენდერავა არნოლდ ერასტოს ძე"),
    (9, 77, "სოხუმის რაიონის", "ხაჯაია ჯემალ ვიქტორის ძე"),
    (10, 79, "ბათუმის", "ნაკაშიძე ბადრი ჯემალის ძე"),
    (10, 80, "ქედის", "ქაჯაია მერაბ მუსას ძე"),
    (10, 81, "ქობულეთის", "ცივაძე დავით გიორგის ძე"),
    (10, 82, "შუახევის", "ქარცივაძე ნოდარ ხემილის ძე"),
    (10, 83, "ხელვაჩაურის", "ცინცაძე თამაზ ისმაილის ძე"),
    (10, 84, "ხულოს", "იაკობიძე ვაჟა ტაშტანის ძე"),
)

# 1995 first round: (district, district as printed, electorate, participants, member elected as printed).
FIRST_ROUND_1995 = (
    (11, "საგარეჯოს", 39_734, 34_439, "თედიაშვილი ლევანი"),
    (18, "ახმეტის", 27_142, 15_878, "ახმეტელი ზურაბი"),
    (19, "თიანეთის", 11_858, 7_480, "მეჭიაური თამაზი"),
    (21, "გარდაბნის", 71_205, 50_154, "მამალაძე ნუგზარ (ხუტა)"),
    (22, "მარნეულის", 67_559, 59_369, "შუბითიძე ამირანი"),
    (23, "ბოლნისის", 45_432, 38_315, "ღვინიაშვილი შუქრი"),
    (24, "დმანისის", 18_521, 14_333, "ტატუაშვილი ალექსი"),
    (25, "წალკის", 17_535, 12_589, "აიდინოვი ვიტალი"),
    (26, "თეთრიწყაროს", 18_465, 13_361, "დალაქიშვილი რომანი"),
    (27, "მცხეთის", 45_444, 26_793, "ზუიკიძე დავითი"),
    (29, "ყაზბეგის", 4_247, 3_118, "მარსაგიშვილი სეველი"),
    (33, "ქარელის", 35_032, 28_618, "თხელიძე თამაზი"),
    (40, "ახალქალაქის", 39_999, 30_864, "რაისიანი მელიქი"),
    (41, "ნინოწმინდის", 22_070, 15_577, "ბდოიანი მელსი"),
    (46, "ლენტეხის", 6_208, 4_647, "ხაბულიანი დილარი"),
    (48, "ხარაგაულის", 20_723, 14_548, "ნავაძე ნუგზარი"),
    (53, "ვანის", 28_143, 17_885, "ბაზიარი ომარი"),
    (58, "წყალტუბოს", 48_159, 35_927, "შენგელია ოთარი"),
    (60, "ოზურგეთის", 64_042, 46_424, "სიორიძე ზაზა"),
    (62, "ჩოხატაურის", 19_648, 17_234, "ჟღენტი დავითი"),
    (63, "აბაშის", 21_179, 15_834, "კაჭარავა გურამი"),
    (64, "სენაკის", 37_684, 33_958, "გადელია დემური"),
    (65, "მარტვილის", 35_832, 22_033, "ინჯია დავითი"),
    (67, "ზუგდიდის", 87_030, 64_301, "ფაცაცია ოთარი"),
    (69, "ჩხოროწყუს", 22_500, 15_097, "ტყებუჩავა ავთანდილი"),
    (70, "ფოთის", 28_635, 18_365, "ბერაია პეტრე"),
    (79, "ბათუმის", 76_565, 47_949, "ცივაძე გივი"),
    (80, "ქედის", 12_426, 11_824, "სურმანიძე ედვარდი"),
    (81, "ქობულეთის", 55_445, 48_663, "კომახიძე გივი"),
    (82, "შუახევის", 12_522, 10_566, "ქიძინიძე ლევანი"),
    (83, "ხელვაჩაურის", 55_709, 47_166, "აბაშიძე ვახტანგი"),
    (84, "ხულოს", 21_520, 20_310, "ვანიძე ალექსანდრე"),
)

# 3 December 1995 votes: (district, district as printed, member elected as printed).
VOTES_DECEMBER_1995 = (
    (1, "მთაწმინდის", "მამუკა არეშიძე"),
    (3, "საბურთალოს", "ილია ხუციშვილი"),
    (12, "გურჯაანის", "მარსელ მამავარიანი"),
    (15, "ლაგოდეხის", "ამირან ცინცაძე"),
    (36, "ბორჯომის", "ვლადიმერ ქარსელაძე"),
    (51, "ზესტაფონის", "ზურაბ ჩხეიძე"),
)

# 1999: (district, district as printed, electorate, participants).
DISTRICTS_1999 = (
    (1, "მთაწმინდის", 57_375, 35_730),
    (2, "ვაკის", 84_761, 52_320),
    (3, "საბურთალოს", 88_400, 51_677),
    (4, "კრწანისის", 33_290, 18_585),
    (5, "ისნის", 75_891, 45_432),
    (6, "სამგორის", 93_566, 48_706),
    (7, "ჩუღურეთის", 41_300, 25_967),
    (8, "დიდუბის", 61_543, 37_107),
    (9, "ნაძალადევის", 101_780, 58_902),
    (10, "გლდანის", 105_714, 57_894),
    (11, "საგარეჯოს", 39_685, 27_983),
    (12, "გურჯაანის", 55_702, 31_759),
    (13, "სიღნაღის", 31_854, 19_698),
    (14, "დედოფლისწყაროს", 21_689, 14_472),
    (15, "ლაგოდეხის", 35_236, 26_212),
    (16, "ყვარლის", 28_962, 20_255),
    (17, "თელავის", 53_201, 34_154),
    (18, "ახმეტის", 32_872, 19_878),
    (19, "თიანეთის", 11_525, 8_416),
    (20, "რუსთავის", 83_510, 42_299),
    (21, "გარდაბნის", 80_355, 51_445),
    (22, "მარნეულის", 86_372, 85_214),
    (23, "ბოლნისის", 50_362, 48_078),
    (24, "დმანისის", 19_961, 11_995),
    (25, "წალკის", 16_230, 12_474),
    (26, "თეთრიწყაროს", 19_618, 17_331),
    (27, "მცხეთის", 44_855, 24_533),
    (28, "დუშეთის", 26_823, 17_103),
    (29, "ყაზბეგის", 4_202, 3_285),
    (30, "კასპის", 39_695, 26_774),
    (31, "ახალგორის", 6_294, 4_567),
    (32, "გორის", 95_940, 72_790),
    (33, "ქარელის", 35_079, 27_887),
    (35, "ხაშურის", 45_292, 31_201),
    (36, "ბორჯომის", 23_674, 17_557),
    (37, "ახალციხის", 32_944, 20_412),
    (38, "ადიგენის", 14_732, 11_869),
    (39, "ასპინძის", 9_610, 7_282),
    (40, "ახალქალაქის", 42_900, 34_714),
    (41, "ნინოწმინდის", 22_092, 15_722),
    (43, "ონის", 7_932, 6_358),
    (44, "ამბროლაურის", 15_527, 10_253),
    (45, "ცაგერის", 13_172, 11_120),
    (46, "ლენტეხის", 3_625, 2_889),
    (47, "მესტიის", 9_541, 7_974),
    (48, "ხარაგაულის", 20_777, 14_582),
    (49, "თერჯოლის", 32_783, 23_967),
    (50, "საჩხერის", 33_471, 19_853),
    (51, "ზესტაფონის", 51_306, 32_616),
    (52, "ბაღდათის", 23_309, 15_800),
    (53, "ვანის", 27_991, 20_817),
    (54, "სამტრედიის", 47_227, 30_642),
    (55, "ხონის", 23_514, 17_358),
    (56, "ჭიათურის", 43_145, 24_680),
    (57, "ტყიბულის", 24_346, 17_168),
    (58, "წყალტუბოს", 53_072, 34_735),
    (59, "ქუთაისის", 138_915, 76_771),
    (60, "ოზურგეთის", 62_100, 45_299),
    (61, "ლანჩხუთის", 30_730, 22_696),
    (62, "ჩოხატაურის", 19_261, 16_197),
    (63, "აბაშის", 23_822, 19_079),
    (64, "სენაკის", 44_456, 29_354),
    (65, "მარტვილის", 33_589, 21_272),
    (66, "ხობის", 32_040, 21_884),
    (67, "ზუგდიდის", 130_392, 72_023),
    (68, "წალენჯიხის", 30_915, 18_868),
    (69, "ჩხოროწყუს", 23_252, 17_317),
    (70, "ფოთის", 34_725, 21_112),
    (79, "ბათუმის", 76_724, 72_859),
    (80, "ქედის", 13_163, 12_928),
    (81, "ქობულეთის", 59_254, 57_308),
    (82, "შუახევის", 13_586, 12_814),
    (83, "ხელვაჩაურის", 60_064, 57_390),
    (84, "ხულოს", 22_061, 21_789),
    (85, "ლიახვის", 9_178, 6_427),
)
REGISTERED_1999, VOTERS_1999 = 3_143_851, 2_133_878
"""The protocol's national electorate and participants (items I and II)."""

WINNERS_1999 = {
    2: "სააკაშვილი მიხეილ",
    3: "ადამია რევაზ",
    4: "ლიპარტელიანი გოგი",
    6: "ჯაყელი გიორგი",
    7: "ლუღუნაშვილი ზეზვა",
    8: "ბარამიძე გიორგი",
    11: "პაპიაშვილი სოლომონ",
    12: "გოლიგაშვილი როლანდ",
    14: "გონაშვილი ბეჟან",
    15: "კაპანაძე დავით",
    17: "ბეციაშვილი გიორგი (გივი)",
    18: "არველაძე რევაზ",
    20: "ტყეშელაშვილი ზურაბ",
    21: "ჯაფარიძე ლეონიდ",
    22: "სულეიმანოვი აზერ",
    23: "ავქოფაშვილი გურამ",
    24: "ციხელაშვილი თეიმურაზ",
    25: "ხრიოიანი იური",
    26: "ბეჟუაშვილი დავით",
    27: "შამანაური არმაზ",
    28: "მოლოდინი მარინა",
    30: "ქარუმიძე ზურაბ",
    31: "ჩიტიშვილი ავთანდილ",
    32: "ყანჩაველი ვახტანგ",
    33: "ინდუაშვილი ალბერტ",
    35: "გელაშვილი ვალერი",
    39: "სურმანიძე ედვარდ",
    40: "რაისიანი მელიქ",
    41: "მკოიანი ენზელ",
    43: "ნოდარიშვილი რევაზ",
    44: "ლეკიშვილი ნიკოლოზ (ნიკო)",
    45: "მუშკუდიანი კარლო",
    47: "პირველი ლევან",
    49: "ბუცხრიკიძე ბეჟან (ზურაბ)",
    50: "ხურციძე თეიმურაზ",
    51: "კოზირიძე დავით",
    52: "მსხვილიძე ზურაბ",
    53: "ტყემელაშვილი მელორ",
    57: "ყიფიანი რევაზ",
    60: "ჩხაიძე ავთანდილ (ზურა)",
    62: "ჟღენტი დავით",
    64: "გადელია დემურ",
    67: "ჯგუშია თენგიზ",
    79: "სმირბა ასლან",
    81: "ჯაფარიძე როსტომ",
    82: "გოგიტიძე ანზორ",
    83: "აბაშიძე ვახტანგ",
    84: "ღორჯომელაძე ოთარ",
    85: "ვახტანგაშვილი გურამ",
}
FIRST_ROUND_WINNERS_1999 = 49
"""Item III of the protocol: 49 members elected in the majoritarian system in the first round."""

# Where each row's figures sit on the page image, in pixels at 300 dpi.
# 1995: two blocks of 16 rows; the first row's centre and the row pitch, and the column spans of the two figures.
ROWS_1995 = (663.0, 19.53)
COLUMNS_1995 = (((150, 262), (262, 332)), ((662, 762), (762, 852)))
# 1999: the ruled lines between rows (76 lines for 75 rows) and the two figure columns.
LINES_1999 = (
    1004, 1027, 1049, 1072, 1094, 1117, 1139, 1161, 1184, 1209, 1229, 1250, 1271, 1291, 1312, 1333, 1354, 1374,
    1395, 1417, 1437, 1458, 1478, 1499, 1519, 1540, 1561, 1582, 1603, 1624, 1644, 1665, 1686, 1707, 1728, 1748,
    1769, 1790, 1810, 1831, 1852, 1873, 1894, 1915, 1936, 1957, 1977, 1998, 2019, 2039, 2060, 2080, 2101, 2122,
    2142, 2163, 2184, 2204, 2225, 2245, 2265, 2285, 2306, 2326, 2347, 2368, 2388, 2408, 2428, 2448, 2469, 2489,
    2510, 2530, 2551, 2571,
)  # fmt: skip
COLUMNS_1999 = ((250, 420), (420, 595))

UNCERTAIN = {
    1992: (2, 6, 13, 21, 33, 41, 54, 58, 60, 61, 76, 80),
    1995: (23, 25, 27, 29, 41, 53, 63),
    1999: (7, 11, 25, 28, 33, 47, 51, 67),
}
"""Districts whose winner's name is faint or blurred on the scan; the reading given is the closest the print allows."""


# ---------------------------------------------------------------------------------------------------------------------
# Fetching


def _pdf(path: Path) -> None:
    if not path.read_bytes().startswith(b"%PDF"):
        raise ValueError(f"{path} is not a PDF")


def fetch() -> None:
    raw.fetch(SOURCE, ADM2, suffix=".geojson")
    raw.fetch(SOURCE, OSM, suffix=".geojson")
    for issue in ISSUES:
        raw.fetch(SOURCE, issue.url, suffix=".pdf", check=_pdf)


def artifact(issue: Issue) -> raw.Artifact:
    return raw.cached(SOURCE, issue.url)


# ---------------------------------------------------------------------------------------------------------------------
# Packages

PARLIAMENT_ELECTIONS = {
    1992: "ge/1992-10-11-parliament",
    1995: "ge/1995-11-05-parliament",
    1999: "ge/1999-10-31-parliament",
}


def package_key(year: int) -> str:
    return f"{SOURCE}/{PARLIAMENT_ELECTIONS[year].removeprefix('ge/')}-districts"


DECEMBER_KEY = f"{SOURCE}/1995-12-03-parliament-districts"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(year) for year in PARLIAMENT_ELECTIONS), DECEMBER_KEY]


def unit(district: int) -> str:
    return key("unit", SCHEME, district)


def _source(p: Package) -> None:
    p.add(
        "source",
        id=CEC,
        kind="official_body",
        publisher="Central Election Commission of Georgia",
        title="Summary protocols of the Central Election Commission as printed in Sakartvelos Respublika "
        "(საქართველოს რესპუბლიკა), from the Iverieli digital library of the National Parliamentary Library of Georgia",
        url=f"{LIBRARY}/handle/1234/5440",
        license="No license stated; official election results published in the government newspaper",
        attribution="Central Election Commission of Georgia; scans by the National Parliamentary Library of Georgia",
    )


# ---------------------------------------------------------------------------------------------------------------------
# Shapes

ADM2 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/GEO/ADM2/"
    "geoBoundaries-GEO-ADM2_simplified.geojson"
)
GEO_SOURCE = key("source", "geoboundaries-adm2")
SLIVER = 1e-5
"""The smallest polygon kept when a city is cut out of a rayon, in square degrees (about a tenth of a square
kilometer)."""
OSM_SOURCE = key("source", "osm.ge.cities")
OSM_RELATIONS = {
    1: 13438811,
    2: 11300449,
    3: 11300446,
    4: 13438809,
    5: 13438808,
    6: 11300436,
    7: 13438810,
    8: 11300445,
    9: 11300438,
    10: 13438812,
    20: 5997314,
    59: 2024547,
    70: 1995348,
    79: 2009237,
}
"""The OpenStreetMap administrative boundary relation that outlines each district with no rayon of its own: the ten
districts of Tbilisi and the cities of Rustavi (20), Kutaisi (59), Poti (70), and Batumi (79). Liakhvi (85) has no
boundary in OpenStreetMap."""
OSM = "https://nominatim.openstreetmap.org/lookup?format=geojson&polygon_geojson=1&osm_ids=" + ",".join(
    f"R{r}" for r in OSM_RELATIONS.values()
)
PLAN_FROM, PLAN_TO = date(1995, 11, 5), date(2004, 3, 28)
"""The plan the shapes belong to: the districts of the 1995 and 1999 elections, until the single-member seats ended."""
BOUNDARY_SET = key("boundary_set", AREA_SCHEME, "gbopen-adm2")

# The rayons of 1995 by district number: the geoBoundaries ADM2 name that outlines each and the English name.
RAYONS = {
    11: ("Sagarejo", "Sagarejo"),
    12: ("Gurjaani", "Gurjaani"),
    13: ("Sighnaghi", "Sighnaghi"),
    14: ("Dedoplis Tskaro", "Dedoplistskaro"),
    15: ("Lagodekhi", "Lagodekhi"),
    16: ("Qvareli", "Kvareli"),
    17: ("Telavi", "Telavi"),
    18: ("Akhmeta", "Akhmeta"),
    19: ("Tianeti", "Tianeti"),
    21: ("Gardabani", "Gardabani"),
    22: ("Marneuli", "Marneuli"),
    23: ("Bolnisi", "Bolnisi"),
    24: ("Dmanisi", "Dmanisi"),
    25: ("Tsalka", "Tsalka"),
    26: ("Tetri Sqaro", "Tetritskaro"),
    27: ("Mtskheta", "Mtskheta"),
    28: ("Dusheti", "Dusheti"),
    29: ("Kazbegi", "Kazbegi"),
    30: ("Kaspi", "Kaspi"),
    31: ("Akhalgori", "Akhalgori"),
    32: ("Gori", "Gori"),
    33: ("Kareli", "Kareli"),
    35: ("Khashuri", "Khashuri"),
    36: ("Borjomi", "Borjomi"),
    37: ("Akhaltsikhe", "Akhaltsikhe"),
    38: ("Adigeni", "Adigeni"),
    39: ("Aspindza", "Aspindza"),
    40: ("Akhalkalaki", "Akhalkalaki"),
    41: ("Ninotsminda", "Ninotsminda"),
    43: ("Oni", "Oni"),
    44: ("Ambrolauri", "Ambrolauri"),
    45: ("Tsageri", "Tsageri"),
    46: ("Lentekhi", "Lentekhi"),
    47: ("Mestia", "Mestia"),
    48: ("Kharagauli", "Kharagauli"),
    49: ("Terjola", "Terjola"),
    50: ("Sachkhere", "Sachkhere"),
    51: ("Zestaponi", "Zestaponi"),
    52: ("Baghdati", "Baghdati"),
    53: ("Vani", "Vani"),
    54: ("Samtredia", "Samtredia"),
    55: ("Khoni", "Khoni"),
    56: ("Chiatura", "Chiatura"),
    57: ("Tkibuli", "Tkibuli"),
    58: ("Tsqaltubo", "Tskaltubo"),
    60: ("Ozurgeti", "Ozurgeti"),
    61: ("Lanchkhuti", "Lanchkhuti"),
    62: ("Chokhatauri", "Chokhatauri"),
    63: ("Abasha", "Abasha"),
    64: ("Senaki", "Senaki"),
    65: ("Martvili", "Martvili"),
    66: ("Khobi", "Khobi"),
    67: ("Zugdidi", "Zugdidi"),
    68: ("Tsalenjikha", "Tsalenjikha"),
    69: ("Chkhorotsku", "Chkhorotsku"),
    80: ("Keda", "Keda"),
    81: ("Kobuleti", "Kobuleti"),
    82: ("Shuakhevi", "Shuakhevi"),
    83: ("Khelvachauri", "Khelvachauri"),
    84: ("Khulo", "Khulo"),
}
"""The districts that voted in 1995 or 1999; the Abkhazian districts of 1992 have no reporting unit."""
CITIES = {
    20: ("Rustavi", "რუსთავი"),
    59: ("Kutaisi", "ქუთაისი"),
    70: ("Poti", "ფოთი"),
    79: ("Batumi", "ბათუმი"),
}
"""The city districts, outlined from OpenStreetMap."""
TBILISI = "Tbilisi"
TBILISI_DISTRICTS = {
    1: "Mtatsminda",
    2: "Vake",
    3: "Saburtalo",
    4: "Krtsanisi",
    5: "Isani",
    6: "Samgori",
    7: "Chughureti",
    8: "Didube",
    9: "Nadzaladevi",
    10: "Gldani",
}
"""The ten districts of Tbilisi, each the single-member district of the same number."""


def rayon_area(district: int) -> str:
    return key("area", AREA_SCHEME, district)


TBILISI_AREA = key("area", AREA_SCHEME, "tbilisi")


def area_of(district: int) -> str | None:
    """The area a district's unit equals, or None when the unit has no shape of its own."""
    return rayon_area(district) if district in RAYONS or district in OSM_RELATIONS else None


def osm_shapes() -> dict[int, shapely.Geometry]:
    """The OpenStreetMap outline of each city district, by district number."""
    districts = {str(rel): d for d, rel in OSM_RELATIONS.items()}
    out: dict[int, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, OSM).path.read_text(encoding="utf-8"))["features"]:
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        out[districts[str(feature["properties"]["osm_id"])]] = polygons(shape)
    if set(out) != set(OSM_RELATIONS):
        raise ValueError(f"OpenStreetMap outlines for {sorted(out)}, expected {sorted(OSM_RELATIONS)}")
    return out


def polygons(shape: shapely.Geometry, smallest: float = SLIVER) -> shapely.Geometry:
    """The polygons of a shape that are larger than a sliver."""
    parts = [g for g in shapely.get_parts(shape) if g.geom_type == "Polygon" and g.area >= smallest]
    return shapely.MultiPolygon(parts) if len(parts) > 1 else parts[0]


def carve(outline: shapely.Geometry, taken: shapely.Geometry) -> shapely.Geometry:
    """An outline less the districts that have an outline of their own."""
    return polygons(shapely.make_valid(outline).difference(taken))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Georgia's rayons of 1995 as outlined by geoBoundaries, for the single-member districts of 1995 to 2003",
    )
    p.external.add(GEORGIA)
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen Georgia ADM2",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), CC BY 4.0",
    )
    artifact = raw.cached(SOURCE, ADM2)
    p.artifact(artifact, GEO_SOURCE)
    p.add(
        "scheme",
        code=AREA_SCHEME,
        polity_id=GEORGIA,
        kind="administrative",
        rank=1,
        noun="rayon",
        code_system=None,
    )
    p.add(
        "source",
        id=OSM_SOURCE,
        kind="academic",
        publisher="OpenStreetMap contributors",
        title="OpenStreetMap administrative boundaries of the districts of Tbilisi and the cities of Rustavi, Kutaisi, "
        "Poti, and Batumi",
        url="https://www.openstreetmap.org/",
        license="ODbL 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1.0/",
        attribution="© OpenStreetMap contributors, ODbL",
    )
    p.artifact(raw.cached(SOURCE, OSM), OSM_SOURCE)
    outlines: dict[str, shapely.Geometry] = {}
    for feature in json.loads(artifact.path.read_text(encoding="utf-8"))["features"]:
        name = feature["properties"]["shapeName"]
        if name in outlines:
            raise ValueError(f"geoBoundaries holds two ADM2 outlines named {name}")
        outlines[name] = shapely.geometry.shape(feature["geometry"])
    cities = osm_shapes()
    taken = shapely.union_all(list(cities.values()))
    for name, outline in outlines.items():
        outlines[name] = carve(outline, taken)

    def shape(area: str, outline: shapely.Geometry, source: str = GEO_SOURCE, quality: str = "academic") -> None:
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=PLAN_FROM,
            valid_to=PLAN_TO,
            geom=shapely.to_wkb(shapely.set_srid(outline, 4326), hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.point_on_surface(shapely.set_srid(outline, 4326)), hex=True, include_srid=True
            ),
            quality=quality,
            source_id=source,
        )

    names = {d: name for d, name, *_ in (*FIRST_ROUND_1995, *DISTRICTS_1999)}
    for district, (adm2, english) in sorted(RAYONS.items()):
        area = rayon_area(district)
        p.add("area", id=area, scheme_code=AREA_SCHEME)
        p.add("area_code", area_id=area, code=str(district), valid_from=None, valid_to=None)
        p.name(area, f"{english} rayon", "en", "english")
        p.name(area, f"{names[district]} რაიონი", "ka", "official")
        shape(area, outlines[adm2])
    for district, shape_ in sorted(cities.items()):
        area = rayon_area(district)
        p.add("area", id=area, scheme_code=AREA_SCHEME)
        p.add("area_code", area_id=area, code=str(district), valid_from=None, valid_to=None)
        if district in CITIES:
            english, georgian = CITIES[district]
            p.name(area, english, "en", "english")
            p.name(area, georgian, "ka", "official")
        else:
            p.name(area, f"{TBILISI_DISTRICTS[district]} district", "en", "english")
            p.name(area, f"{names[district]} რაიონი", "ka", "official")
        shape(area, shape_, OSM_SOURCE, "osm")
    p.add("area", id=TBILISI_AREA, scheme_code=AREA_SCHEME)
    p.add("area_code", area_id=TBILISI_AREA, code="tbilisi", valid_from=None, valid_to=None)
    p.name(TBILISI_AREA, "Tbilisi", "en", "english")
    p.name(TBILISI_AREA, "თბილისი", "ka", "official")
    shape(TBILISI_AREA, shapely.union_all([cities[d] for d in TBILISI_DISTRICTS]), OSM_SOURCE, "osm")
    p.add(
        "boundary_set",
        id=BOUNDARY_SET,
        scheme_code=AREA_SCHEME,
        vintage="geoBoundaries gbOpen ADM2, read as the rayons of 1995, with the cities and the districts of Tbilisi "
        "outlined from OpenStreetMap and cut out of them",
        valid_from=PLAN_FROM,
        valid_to=PLAN_TO,
        as_of=PLAN_FROM,
    )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Georgia's single-member electoral districts of 1995 and 1999 as reporting units",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {GEORGIA, COUNTRY}
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=GEORGIA,
        kind="electoral_district",
        rank=1,
        noun="single-member district",
        code_system=None,
    )
    names: dict[int, str] = {}
    for district, name, *_ in (*FIRST_ROUND_1995, *DISTRICTS_1999):
        if names.setdefault(district, name) != name:
            raise ValueError(f"district {district} is printed as {names[district]} and {name}")
    for district, name in sorted(names.items()):
        id = unit(district)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=GEORGIA,
            scheme_code=SCHEME,
            code=str(district),
            area_relation="equals" if area_of(district) else "within",
            area_id=area_of(district) or COUNTRY,
            kind="standard",
        )
        p.name(id, f"№{district} {name} საარჩევნო ოლქი", "ka", "official")
        p.name(id, f"Single-member district No. {district}", "en", "english")
    return p


@dataclass(frozen=True)
class Row:
    district: int
    district_name: str
    winner: str | None
    figures: tuple[tuple[int, int, tuple[float, float, float, float]], ...]
    """(measure, value, box in pixels) for each printed figure."""


def rows(year: int) -> list[Row]:
    if year == 1992:
        return [Row(d, name, winner, ()) for _, d, name, winner in WINNERS_1992]
    if year == 1995:
        out = []
        first, pitch = ROWS_1995
        for i, (d, name, registered, voters, winner) in enumerate(FIRST_ROUND_1995):
            if voters > registered:
                raise ValueError(f"1995 district {d}: {voters} participants of {registered}")
            (rx, vx) = COLUMNS_1995[i // 16]
            y = first + pitch * (i % 16)
            out.append(
                Row(
                    d,
                    name,
                    winner,
                    (
                        (REGISTERED, registered, (rx[0], y - 10, rx[1], y + 10)),
                        (VOTERS, voters, (vx[0], y - 10, vx[1], y + 10)),
                    ),
                )
            )
        return out
    if sum(r for _, _, r, _ in DISTRICTS_1999) != REGISTERED_1999:
        raise ValueError("the 1999 district electorates do not add up to the protocol's national electorate")
    if sum(v for _, _, _, v in DISTRICTS_1999) != VOTERS_1999:
        raise ValueError("the 1999 district participants do not add up to the protocol's national participants")
    if len(WINNERS_1999) != FIRST_ROUND_WINNERS_1999 or not set(WINNERS_1999) <= {d for d, *_ in DISTRICTS_1999}:
        raise ValueError("the 1999 first-round winners do not match the protocol")
    if len(LINES_1999) != len(DISTRICTS_1999) + 1:
        raise ValueError("the 1999 row lines do not match the rows")
    out = []
    for i, (d, name, registered, voters) in enumerate(DISTRICTS_1999):
        top, bottom = LINES_1999[i], LINES_1999[i + 1]
        (rx, vx) = COLUMNS_1999
        out.append(
            Row(
                d,
                name,
                WINNERS_1999.get(d),
                (
                    (REGISTERED, registered, (rx[0], top, rx[1], bottom)),
                    (VOTERS, voters, (vx[0], top, vx[1], bottom)),
                ),
            )
        )
    return out


ISSUE_OF = {1992: ISSUE_1992, 1995: ISSUE_1995, 1999: ISSUE_1999}
DAYS = {1992: date(1992, 10, 11), 1995: date(1995, 11, 5), 1999: date(1999, 10, 31)}
PAGE_SIZES = {1992: (2421, 3368), 1995: (2352, 3265), 1999: (1962, 3211)}
"""Each page image's size in pixels at 300 dpi."""

METHODS = {
    1992: (
        "The member elected in each single-member district, from the Central Election Commission's announcement of the "
        "results of 11 October 1992, printed in Sakartvelos Respublika No. 222 of 28 October 1992, page 1 (section "
        "II). The announcement names the district and the member elected, with the member's patronymic, and prints "
        "no votes, electorate, or turnout for the districts, so each result records the declared winner only. The "
        "page is a scan with no text layer; the names were read from the page image."
    ),
    1995: (
        "The first round of 5 November 1995 in the 32 single-member districts that elected a member outright, from "
        "the Central Election Commission's results printed in Sakartvelos Respublika No. 171 of 21 November 1995, "
        "page 3 (section 1): each district's electorate, the voters who took part, and the member elected. The page "
        "prints no candidate's votes. Of the 85 districts, 77 voted; 40 went to a runoff on 19 November 1995, the "
        "vote failed for want of turnout in district 20 (Rustavi), and the results of polling stations in districts "
        "1 and 15 were annulled. Those districts are not stored here. The page is a scan with no text layer; the "
        "figures and names were read from the page image."
    ),
    1999: (
        "The electorate and the voters who took part in each of the 75 single-member districts that voted on 31 "
        "October 1999, and the 49 members elected in the first round, from the Central Election Commission's summary "
        "protocol of 7 November 1999, printed in Sakartvelos Respublika No. 304 of 9 November 1999, page 5 (sections "
        "IV and V). The district figures add up exactly to the protocol's national electorate (3,143,851) and "
        "participants (2,133,878). The protocol prints no candidate's votes. The 26 districts without a winner went "
        "to the runoff of 14 November 1999, which is not stored here. The page is a scan with no text layer; the "
        "figures and names were read from the page image."
    ),
}


def build(year: int) -> Package:
    slug = PARLIAMENT_ELECTIONS[year]
    election = cec_1990s.key("election", slug)
    issue = ISSUE_OF[year]
    p = Package(
        package_key(year),
        SOURCE,
        f"Georgia's {year} Parliament single-member districts: the members elected"
        + (", with each district's electorate and participants," if year != 1992 else "")
        + " from the Central Election Commission as printed in Sakartvelos Respublika",
        # The election and the Parliament come from the ge.cec_1990s pipeline's packages.
        depends=[
            f"{SOURCE}/registry",
            f"{cec_1990s.SOURCE}/{slug.removeprefix('ge/')}",
            f"{cec_1990s.SOURCE}/registry",
        ],
    )
    p.external |= {GEORGIA, COUNTRY}
    _source(p)
    art = artifact(issue)
    artifact_id = p.artifact(art, CEC)
    page = key("page", year, issue.page)
    width, height = PAGE_SIZES[year]
    p.add(
        "source_page",
        id=page,
        artifact_id=artifact_id,
        page_number=issue.page,
        image_key=f"pages/{SOURCE}/{art.sha256}-{issue.page}.png",
        width=round(width * POINTS),
        height=round(height * POINTS),
    )
    uncertain = UNCERTAIN[year]
    values: list[dict[str, object]] = []
    for row in rows(year):
        contest = key("contest", year, row.district)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=cec_1990s.PARLIAMENT,
            magnitude=1,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} single-member district No. {row.district}", "en", "english")
        p.name(contest, f"№{row.district} {row.district_name} საარჩევნო ოლქი", "ka", "official")
        p.add("slug", path=f"{slug}/district-{row.district}", entity_id=contest, is_current=True)
        rs = key("result_set", year, row.district)
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CEC,
            status="active",
            is_default=True,
            completeness=0.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHODS[year], lang="en")
        p.add(
            "citation",
            id=key("citation", rs),
            entity_id=rs,
            source_id=CEC,
            locator={"url": issue.url, "landing": issue.landing, "issue": issue.printed, "pdf_pages": [issue.page]},
        )
        if row.winner is not None:
            person = key("person", year, row.district)
            p.add("person", id=person)
            p.name(person, row.winner, "ka", "official")
            option = key("option", year, row.district)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_label=row.winner,
            )
            candidacy = key("candidacy", year, row.district)
            p.add("candidacy", id=candidacy, person_id=person, option_id=option, role="head", ballot_name=row.winner)
            p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
            if row.district in uncertain:
                p.add(
                    "note",
                    id=key("note", rs, "caveat"),
                    entity_id=rs,
                    kind="caveat",
                    body_md="The winner's name is faint or blurred on the scanned page; the spelling given is the "
                    "closest reading the print allows and may differ from the official spelling.",
                    lang="en",
                )
        for measure, value, (x0, y0, x1, y1) in row.figures:
            values.append(
                {
                    "result_set_id": rs,
                    "unit_id": unit(row.district),
                    "measure": measure,
                    "value": value,
                    "page_id": page,
                    "x0": x0 * POINTS,
                    "y0": y0 * POINTS,
                    "x1": x1 * POINTS,
                    "y1": y1 * POINTS,
                }
            )
    for v in values:
        fact = {
            "result_set_id": v["result_set_id"],
            "unit_id": v["unit_id"],
            "option_id": None,
            "channel": 0,
            "measure": v["measure"],
            "count_round": 0,
        }
        p.add("result_value", **fact, flags=0, value=v["value"])
        p.add(
            "value_locator",
            **fact,
            page_id=v["page_id"],
            source_id=CEC,
            x0=v["x0"],
            y0=v["y0"],
            x1=v["x1"],
            y1=v["y1"],
        )
    return p


DECEMBER_METHOD = (
    "The member elected in each of the six single-member districts that voted again on 3 December 1995, from the "
    "Central Election Commission's report of its session of 7 December 1995, printed in Sakartvelos Respublika No. "
    "183 of 8 December 1995, page 1. In districts 1 (Mtatsminda) and 15 (Lagodekhi) the second round was held at "
    "every precinct; in districts 3, 12, 36, and 51 the voting was repeated at some precincts. The report names the "
    "member elected and prints no votes, electorate, or turnout, so the result records the declared winner only. The "
    "first-round results of districts 1 and 15 and the 19 November 1995 runoff are not stored here, so the contests "
    "do not point to an earlier round. The page is a scan with no text layer; the names were read from the page "
    "image."
)


def december() -> Package:
    election = cec_1990s.key("election", PARLIAMENT_ELECTIONS[1995])
    issue = ISSUE_1995_DECEMBER
    p = Package(
        DECEMBER_KEY,
        SOURCE,
        "Georgia's 1995 Parliament single-member districts that voted again on 3 December 1995: the members elected, "
        "from the Central Election Commission as printed in Sakartvelos Respublika",
        depends=[
            f"{SOURCE}/registry",
            f"{cec_1990s.SOURCE}/{PARLIAMENT_ELECTIONS[1995].removeprefix('ge/')}",
            f"{cec_1990s.SOURCE}/registry",
        ],
    )
    p.external |= {GEORGIA, COUNTRY}
    _source(p)
    art = raw.cached(SOURCE, ISSUE_1995_DECEMBER_COPY)
    artifact_id = p.artifact(art, CEC)
    page = key("page", "1995-12", issue.page)
    p.add(
        "source_page",
        id=page,
        artifact_id=artifact_id,
        page_number=issue.page,
        image_key=f"pages/{SOURCE}/{art.sha256}-{issue.page}.png",
        width=round(2352 * POINTS),
        height=round(3265 * POINTS),
    )
    for district, name, winner in VOTES_DECEMBER_1995:
        contest = key("contest", 1995, district, "round-2")
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=cec_1990s.PARLIAMENT,
            magnitude=1,
            round_number=2,
            default_result_kind="counted",
        )
        p.name(contest, f"1995 single-member district No. {district}, second round", "en", "english")
        p.name(contest, f"№{district} {name} საარჩევნო ოლქი, მეორე ტური", "ka", "official")
        p.add(
            "slug", path=f"{PARLIAMENT_ELECTIONS[1995]}/district-{district}-round-2", entity_id=contest, is_current=True
        )
        rs = key("result_set", 1995, district, "round-2")
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CEC,
            status="active",
            is_default=True,
            completeness=0.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=DECEMBER_METHOD, lang="en")
        p.add(
            "citation",
            id=key("citation", rs),
            entity_id=rs,
            source_id=CEC,
            locator={
                "url": issue.url,
                "landing": issue.landing,
                "issue": issue.printed,
                "pdf_pages": [issue.page],
                "copy": ISSUE_1995_DECEMBER_COPY,
            },
        )
        person = key("person", 1995, district, "round-2")
        p.add("person", id=person)
        p.name(person, winner, "ka", "official")
        option = key("option", 1995, district, "round-2")
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=winner)
        p.add(
            "candidacy",
            id=key("candidacy", 1995, district, "round-2"),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=winner,
        )
        p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for year in PARLIAMENT_ELECTIONS:
        build(year).write()
    december().write()


def emit() -> None:
    package.run("load", packages())
