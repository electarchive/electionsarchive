"""The parties and the independent label that the 2010 and 2015 results tables print, in the Burmese the UEC prints
them, with the English name where one is well known."""

import re

# Spelling-insensitive key: the tables spell one party several ways, with spacing, quote marks, and a short
# abbreviation in parentheses after some names.
DROP = re.compile(r"\([^)]*(?:\)|$)|[\s\u200b\u1037\u103a]|\]\]|\}\}|[\"\u201c\u201d]")
TYPOS = (
    ("အမျိုးသမီးပါတီ", "အမျိုးသမီးပါတီ"),
    ("ကြံခိုင်", "ကြံ့ခိုင်"),
    ("ဖွံဖြိုး", "ဖွံ့ဖြိုး"),
    ("အမျိုသား", "အမျိုးသား"),
    ("ဒိီမို", "ဒီမို"),
    ("တိုးကက်", "တိုးတက်"),
    ("တသီးပုဂ္ဂလ", "တစ်သီးပုဂ္ဂလ"),
    ("တိုင်းရင်သား", "တိုင်းရင်းသား"),
)


def norm(name: str) -> str:
    text = DROP.sub("", name)
    for wrong, right in TYPOS:
        text = text.replace(wrong, right)
    return text.removesuffix("ပါတီ").removesuffix("ပါတ")


PARTIES: dict[str, tuple[str, str | None, str]] = {}


def add(code: str, burmese: str, english: str | None = None) -> None:
    PARTIES[norm(burmese)] = (code, english, burmese)


add("usdp", "ပြည်ထောင်စုကြံ့ခိုင်ရေးနှင့်ဖွံ့ဖြိုးရေးပါတီ", "Union Solidarity and Development Party")
add("nup", "တိုင်းရင်းသားစည်းလုံးညီညွတ်ရေးပါတီ", "National Unity Party")
add("nld", "အမျိုးသားဒီမိုကရေစီအဖွဲ့ချုပ်ပါတီ", "National League for Democracy")
add("ndf", "အမျိုးသားဒီမိုကရေစီအင်အားစုပါတီ", "National Democratic Force")
add("ndp", "အမျိုးသားတိုးတက်ရေးပါတီ", "National Development Party")
add("mfdp", "မြန်မာနိုင်ငံတောင်သူလယ်သမားဖွံ့ဖြိုးတိုးတက်ရေးပါတီ", "Myanmar Farmers Development Party")
add("sndp", "ရှမ်းတိုင်းရင်းသားများဒီမိုကရက်တစ်ပါတီ", "Shan Nationalities Democratic Party")
add("snld", "ရှမ်းတိုင်းရင်းသားများဒီမိုကရေစီအဖွဲ့ချုပ်ပါတီ", "Shan Nationalities League for Democracy")
add("kpp", "ကရင်ပြည်သူ့ပါတီ", "Kayin People's Party")
add("dpm", "ဒီမိုကရက်တစ်ပါတီ(မြန်မာ)", "Democratic Party (Myanmar)")
add("anp", "ရခိုင်အမျိုးသားပါတီ", "Arakan National Party")
add("kssdp", "ကချင်ပြည်နယ်ဒီမိုကရေစီပါတီ", "Kachin State Democracy Party")
add("cndp", "ချင်းအမျိုးသားဒီမိုကရက်တစ်ပါတီ", "Chin National Democratic Party")
add("mnp", "မွန်အမျိုးသားပါတီ", "Mon National Party")
add("fup", "ဖက်ဒရယ်ပြည်ထောင်စုပါတီ", "Federal Union Party")
add("amrdp", "မွန်ဒေသလုံးဆိုင်ရာဒီမိုကရေစီပါတီ", "All Mon Region Democracy Party")
add("mpp", "ခေတ်သစ်ပြည်သူ့ပါတီ", "Modern People's Party")
add("kncd", "ကချင်အမျိုးသားများဒီမိုကရေစီကွန်ဂရက်ပါတီ", "Kachin National Congress for Democracy")
add("psdp", "ဖလုံ-စဝေါ်ဒီမိုကရက်တစ်ပါတီ", "Phalon-Sawaw Democratic Party")
add("udp", "ညီညွတ်သောဒီမိုကရက်တစ်ပါတီ", "United Democratic Party")
add("kdp", "ကချင်ဒီမိုကရက်တစ်ပါတီ", "Kachin Democratic Party")
add("lndp", "လားဟူအမျိုးသားဖွံ့ဖြိုးတိုးတက်ရေးပါတီ", "Lahu National Development Party")
add("tndp", "တိုင်းလိုင်(ရှမ်းနီ)အမျိုးသားများဖွံ့ဖြိုးတိုးတက်ရေးပါတီ", "Tai-Leng (Red Shan) Nationalities Development Party")
add("g88", "88မျိုးဆက်ကျောင်းသားလူငယ်များ(ပြည်ထောင်စုမြန်မာနိုင်ငံ)ပါတီ", "88 Generation Student Youths (Union of Myanmar)")
add("chp", "ချင်းတိုးတက်ရေးပါတီ", "Chin Progressive Party")
add("unfnp", "ပြည်ထောင်စုမြန်မာနိုင်ငံအမျိုးသားနိုင်ငံရေးအဖွဲ့ချုပ်ပါတီ", "Union of Myanmar Federation of National Politics")
add("tnp", "တအာင်း(ပလောင်)အမျိုးသားပါတီ", "Ta'Arng (Palaung) National Party")
add("cld", "ချင်းဒီမိုကရေစီအဖွဲ့ချုပ်ပါတီ", "Chin League for Democracy")
add("lnudp", "လော်ဝေါ်အမျိုးသားစည်းလုံးညီညွတ်ရေးနှင့်ဖွံ့ဖြိုးရေးပါတီ", None)
add("lisu", "လီဆူအမျိုးသားဖွံ့ဖြိုးတိုးတက်ရေးပါတီ(ဒူးလေးပါတီ)", "Lisu National Development Party")
add("nndp", "အမျိုးသားဒီမိုကရေစီပါတီသစ်ပါတီ", "New National Democracy Party")
add("kasnp", "လူမျိုးပေါင်းစုံဒီမိုကရေစီပါတီ(ကယားပြည်နယ်)", "Kayah State All Nationalities Democracy Party")
add("pdup", "မတူကွဲပြားခြင်းနှင့်ငြိမ်းချမ်းရေးပါတီ", "Peace and Diversity Party")
add("rsnf", "ရခိုင်ပြည်နယ်အမျိုးသားအင်အားစုပါတီ", None)
add("knp", "ကရင်အမျိုးသားပါတီ", "Kayin National Party")
add("dnp", "ဓနုတိုင်းရင်းသားလူမျိုးများဒီမိုကရေစီပါတီ", None)
add("kedp", "ကယားလူမျိုးစုဒီမိုကရေစီပါတီ", None)
add("nsdp", "လူ့ဘောင်သစ်ဒီမိုကရက်တစ်ပါတီ", "New Society Democratic Party")
add("rpp", "ရခိုင်မျိုးချစ်ပါတီ", "Rakhine Patriotic Party")
add("kyan", "ကယန်းအမျိုးသားပါတီ", "Kayan National Party")
add("upno", "ပြည်ထောင်စုပအိုဝ်းအမျိုးသားအဖွဲ့ချုပ်ပါတီ", "Union Pa-O National Organization")
add("pno", "ပအိုဝ်းအမျိုးသားအဖွဲ့ချုပ်ပါတီ", "Pa-O National Organization")
add("wdp", "ဝဒီမိုကရက်တစ်ပါတီ", "Wa Democratic Party")
add("dpp", "ဒီမိုကရေစီနှင့်ငြိမ်းချမ်းရေးပါတီ", "Democracy and Peace Party")
add("mnsdp", "မြန်မာ့လူဘောင်သစ်ဒီမိုကရက်တစ်ပါတီ", "Myanmar New Society Democratic Party")
add("rndp", "ရခိုင်တိုင်းရင်းသားများတိုးတက်ရေးပါတီ", None)
add("dtnp", "ထားဝယ်တိုင်းရင်းသားများပါတီ", None)
add("nucp", "အမျိုးသားညီညွတ်ရေးကွန်ဂရက်ပါတီ", "National Unity Congress Party")
add("kdup", "ရှမ်းပြည်ကိုးကန့်ဒီမိုကရက်တစ်ပါတီ", None)
add("wnudp", "ဝအမျိုးသားစည်းလုံးညီညွတ်ရေးပါတီ", None)
add("kdp2", "ကရင်ဒီမိုကရက်တစ်ပါတီ", "Kayin Democratic Party")
add("zcd", "ဇိုမီးဒီမိုကရေစီအဖွဲ့ချုပ်ပါတီ", "Zomi Congress for Democracy")
add("nsp", "လူ့ဘောင်သစ်ပါတီ", "New Society Party")
add("udpp", "ပြည်ထောင်စုဒီမိုကရေစီပါတီ", None)
add("mfwpp", "မြန်မာနိုင်ငံတောင်သူလယ်သမားအလုပ်သမားပြည်သူ့ပါတီ", None)
add("kndup", "ကိုးကန့်ဒီမိုကရေစီနှင့်ညီညွတ်ရေးပါတီ", "Kokang Democracy and Unity Party")
add("ankp", "အရှိုချင်းအမျိုးသားပါတီ", None)
add("pdp", "ပြည်သူ့ဒီမိုကရေစီပါတီ", "People's Democracy Party")
add("dnoo", "ဓနုအမျိုးသားများအဖွဲ့ချုပ်ပါတီ", None)
add("c_np", "ချင်းအမျိုးသားပါတီ", "Chin National Party")
add("unad", "စည်းလုံးညီညွတ်ရေးနှင့်ဒီမိုကရေစီပါတီကချင်ပြည်နယ်", "Unity and Democracy Party of Kachin State")
add("nal", "အမျိုးသားနိုင်ငံရေးမဟာမိတ်များအဖွဲ့ချုပ်", None)
add("ndpd", "အမျိုးသားဖွံ့ဖြိုးတိုးတက်ရေးဒီမိုကရက်တစ်ပါတီ", None)
add("zrdp", "ဇိုတိုင်းရင်းသားဒေသဖွံ့ဖြိုးရေးပါတီ", None)
add("ufap", "ပြည်ထောင်စုတောင်သူလယ်သမားအင်အားစုပါတီ", None)
add("kmndp", "ခမီအမျိုးသားဖွံ့ဖြိုးတိုးတက်ရေးပါတီ", None)
add("kmtp", "ကမန်အမျိုးသားတိုးတက်ရေးပါတီ", None)
add("sedp", "ရှမ်းပြည်နယ်အရှေ့ပိုင်းဖွံ့ဖြိုးတိုးတက်ရေးဒီမိုကရက်တစ်ပါတီ", None)
add("andp", "အာခါအမျိုးသားဖွံ့ဖြိုးတိုးတက်ရေးပါတီ", "Akha National Development Party")
add("lpk", "လမ်းပြကြယ်ပါတီ", None)
add("afp", "မဟာမိတ်တောင်သူလယ်သမားပါတီ", None)
add("mncp", "မြန်မာအမျိုးသားကွန်ဂရက်ပါတီ", "Myanmar National Congress Party")
add("pcsdp", "ပြည်သူ့အကျိုးပြုကျောင်းသားများဒီမိုကရေစီပါတီ", "Public Contribute Students Democracy Party")
add("indp", "အင်းအမျိုးသားဖွံ့ဖြိုးတိုးတက်ရေးပါတီ", "Inn National Development Party")
add("kndp", "ကရင်ပြည်နယ်ဒီမိုကရေစီနှင့်ဖွံ့ဖြိုးတိုးတက်ရေးပါတီ", None)
add("dnp2", "တိုင်းရင်းသားလူမျိုးစုများဖွံ့ဖြိုးရေးပါတီ", None)
add("wsnp", "ဝံသာနုဒီမိုကရက်တစ်ပါတီ", None)
add("mdp", "မြိုတိုင်းရင်းသားဖွံ့ဖြိုးရေးပါတီ", None)
add("dnup", "ခူမီး(ခမီး)အမျိုးသားပါတီ", None)
add("mup", "ခေတ်သစ်ပြည်ထောင်စုပါတီ", None)
add("inup", "အင်းတိုင်းရင်းသားအဖွဲ့ချုပ်ပါတီ", None)
add("dhrp", "ဒီမိုကရေစီနှင့်လူ့အခွင့်အရေးပါတီ", None)
add("wnp", "ဝံသာနုပါတီ", None)
INDEPENDENT = norm("တစ်သီးပုဂ္ဂလ")
