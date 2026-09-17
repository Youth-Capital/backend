"""Seed the platform's soft-skill assessment.

Reference data, not demo data — this test is part of the product the way the
skill taxonomy is, so it lives beside `seed_taxonomy` rather than beside
`seed_demo`, and a production deploy runs it.

Two things about the content are deliberate.

*Every option is defensible.* No answer is a trap and none is obviously the
"right" one to a person trying to game it: escalating a blocked task to a
manager immediately is not wrong, it just shows less independence than trying
first. That is what the weights encode — how much of the competency the action
demonstrates — and it is why the test can show its explanations afterwards
without becoming solvable.

*The situations are local.* A student in Namangan reads about a group project
at their college and an internship at a firm in Tashkent, not about a Series B
stand-up. A scenario someone cannot picture measures reading comprehension.

Re-runnable: questions are matched on their order within the test, so editing a
scenario here and re-running updates it rather than piling up a second copy.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.assessment.models import (
    AnswerOption,
    Question,
    QuestionType,
    Test,
    TestSkill,
    TestType,
)
from apps.common.enums import ModerationStatus
from apps.taxonomy.models import Skill

#: One test per interface language. `Test.language` holds a single language,
#: so a trilingual assessment is three tests carrying the same scenarios —
#: matching how every other test on the platform is authored.
TITLES = {
    "uz": "Yumshoq ko'nikmalar baholovchisi",
    "ru": "Оценка гибких навыков",
    "en": "Soft skills assessment",
}

DESCRIPTIONS = {
    "uz": "Vaziyatga asoslangan baholash. To'g'ri javob yo'q — har bir variant "
    "real harakat, faqat ular turli darajada ko'nikmani ko'rsatadi.",
    "ru": "Ситуационная оценка. Правильного ответа нет — каждый вариант это "
    "реальное действие, они лишь по-разному раскрывают компетенцию.",
    "en": "A situational assessment. There is no right answer — every option "
    "is a real action; they differ in how much of the competency they show.",
}

#: (skill slug, uz, ru, en) — a scenario, then four actions with the share of
#: the competency each one demonstrates (0-100).
SCENARIOS: list[dict] = [
    {
        "skill": "communication",
        "text": {
            "uz": "Loyiha bo'yicha muhim ma'lumotni noto'g'ri tushunib, "
            "jamoaga xato yetkazdingiz. Buni ertasi kuni bildingiz. Nima "
            "qilasiz?",
            "ru": "Вы неверно поняли важную информацию по проекту и передали "
            "команде ошибку. Обнаружили это на следующий день. Ваши действия?",
            "en": "You misunderstood key project information and passed the "
            "error on to your team. You notice the next day. What do you do?",
        },
        "options": [
            (
                100,
                {
                    "uz": "Darhol jamoaga xabar beraman, xatoni tushuntiraman "
                    "va to'g'ri ma'lumotni yuboraman.",
                    "ru": "Сразу сообщаю команде, объясняю ошибку и присылаю "
                    "верную информацию.",
                    "en": "Tell the team at once, explain the error and send "
                    "the correct information.",
                },
            ),
            (
                60,
                {
                    "uz": "Avval rahbarim bilan maslahatlashaman, keyin "
                    "jamoaga aytaman.",
                    "ru": "Сначала советуюсь с руководителем, потом говорю "
                    "команде.",
                    "en": "Check with my manager first, then tell the team.",
                },
            ),
            (
                30,
                {
                    "uz": "Keyingi yig'ilishda mavzu ochilganda aytaman.",
                    "ru": "Скажу на следующей встрече, когда зайдёт речь.",
                    "en": "Mention it at the next meeting if it comes up.",
                },
            ),
            (
                0,
                {
                    "uz": "Hech kim sezmagan bo'lsa, o'zim jimgina "
                    "to'g'rilayman.",
                    "ru": "Если никто не заметил, тихо исправлю сам.",
                    "en": "If nobody noticed, quietly fix it myself.",
                },
            ),
        ],
    },
    {
        "skill": "communication",
        "text": {
            "uz": "Mijoz sizga texnik bo'lmagan tilda tushuntirish kerak "
            "bo'lgan savol berdi. Javobingiz murakkab. Qanday yo'l tutasiz?",
            "ru": "Клиент задал вопрос, ответ на который сложен и требует "
            "объяснения без технических терминов. Как поступите?",
            "en": "A client asks something whose answer is technical and needs "
            "explaining in plain language. How do you handle it?",
        },
        "options": [
            (
                100,
                {
                    "uz": "Oddiy misol orqali tushuntiraman va tushunganini "
                    "tekshirish uchun savol beraman.",
                    "ru": "Объясняю на простом примере и проверяю вопросом, "
                    "понял ли он.",
                    "en": "Explain with a simple example, then check "
                    "understanding with a question.",
                },
            ),
            (
                65,
                {
                    "uz": "Qisqa va sodda javob beraman, savollari bo'lsa "
                    "so'rashini aytaman.",
                    "ru": "Отвечаю коротко и просто, предлагаю задать вопросы.",
                    "en": "Answer briefly and simply, invite questions.",
                },
            ),
            (
                35,
                {
                    "uz": "To'liq texnik javobni yozib yuboraman — hammasi "
                    "aniq bo'lsin.",
                    "ru": "Пишу полный технический ответ — пусть будет точно.",
                    "en": "Send the full technical answer so nothing is lost.",
                },
            ),
            (
                15,
                {
                    "uz": "Bu savolni tushuntira oladigan hamkasbimga "
                    "yo'naltiraman.",
                    "ru": "Переадресую коллеге, который умеет объяснять.",
                    "en": "Pass it to a colleague who is better at explaining.",
                },
            ),
        ],
    },
    {
        "skill": "teamwork",
        "text": {
            "uz": "Guruh loyihasida bir a'zo o'z qismini bajarmayapti va "
            "topshirish muddati yaqin. Nima qilasiz?",
            "ru": "В групповом проекте один участник не делает свою часть, а "
            "срок близко. Ваши действия?",
            "en": "In a group project one member is not doing their part and "
            "the deadline is close. What do you do?",
        },
        "options": [
            (
                100,
                {
                    "uz": "U bilan shaxsan gaplashaman: sabab nima, qanday "
                    "yordam kerak — keyin rejani moslashtiramiz.",
                    "ru": "Говорю с ним лично: в чём причина, чем помочь — и "
                    "корректируем план.",
                    "en": "Talk to them privately: what is blocking them, what "
                    "help is needed — then adjust the plan.",
                },
            ),
            (
                55,
                {
                    "uz": "Jamoa chatida umumiy holatni ko'taraman, ismini "
                    "aytmayman.",
                    "ru": "Поднимаю вопрос в общем чате, не называя имени.",
                    "en": "Raise it in the group chat without naming them.",
                },
            ),
            (
                40,
                {
                    "uz": "O'qituvchiga yoki rahbarga aytaman.",
                    "ru": "Сообщаю преподавателю или руководителю.",
                    "en": "Tell the teacher or the manager.",
                },
            ),
            (
                20,
                {
                    "uz": "Uning qismini o'zim bajaraman, muddat muhimroq.",
                    "ru": "Делаю его часть сам — срок важнее.",
                    "en": "Do their part myself; the deadline matters more.",
                },
            ),
        ],
    },
    {
        "skill": "teamwork",
        "text": {
            "uz": "Jamoa sizning taklifingizni rad etib, boshqa yo'lni "
            "tanladi. Siz baribir o'z variantingizni to'g'ri deb bilasiz.",
            "ru": "Команда отклонила ваше предложение и выбрала другой путь. "
            "Вы по-прежнему считаете свой вариант верным.",
            "en": "The team rejected your proposal and chose another route. "
            "You still think yours was right.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Qarorni qo'llab-quvvatlab ishlayman, lekin "
                    "xavflarni yozib qo'yaman va kelishilgan nuqtada qayta "
                    "ko'rib chiqishni taklif qilaman.",
                    "ru": "Работаю по общему решению, но фиксирую риски и "
                    "предлагаю вернуться к вопросу в оговорённой точке.",
                    "en": "Back the decision and work on it, but write down "
                    "the risks and propose a checkpoint to revisit.",
                },
            ),
            (
                70,
                {
                    "uz": "Qarorni qabul qilaman va boshqa gapirmayman.",
                    "ru": "Принимаю решение и больше не возвращаюсь к теме.",
                    "en": "Accept the decision and let it go.",
                },
            ),
            (
                30,
                {
                    "uz": "Ishlayman, lekin bu men aytgan yo'l emasligini "
                    "eslatib turaman.",
                    "ru": "Работаю, но напоминаю, что предлагал иначе.",
                    "en": "Work on it, but keep reminding them I proposed "
                    "otherwise.",
                },
            ),
            (
                10,
                {
                    "uz": "O'z variantimni yon tarafda davom ettiraman.",
                    "ru": "Продолжаю делать по-своему параллельно.",
                    "en": "Carry on with my own version on the side.",
                },
            ),
        ],
    },
    {
        "skill": "leadership",
        "text": {
            "uz": "Sizga birinchi marta kichik guruhga rahbarlik topshirildi. "
            "Birinchi qadamingiz?",
            "ru": "Вам впервые поручили руководить небольшой группой. Первый "
            "шаг?",
            "en": "You are put in charge of a small group for the first time. "
            "Your first step?",
        },
        "options": [
            (
                100,
                {
                    "uz": "Har biri bilan gaplashib, kim nimani yaxshi "
                    "biladi va nimani xohlaydi — shunga qarab rollarni "
                    "taqsimlayman.",
                    "ru": "Говорю с каждым: кто что умеет и чего хочет — и "
                    "распределяю роли по этому.",
                    "en": "Talk to each person about what they are good at and "
                    "want, then split the roles accordingly.",
                },
            ),
            (
                60,
                {
                    "uz": "Aniq reja tuzib, vazifalarni taqsimlab yuboraman.",
                    "ru": "Составляю чёткий план и раздаю задачи.",
                    "en": "Write a clear plan and hand out the tasks.",
                },
            ),
            (
                40,
                {
                    "uz": "Jamoa o'zi taqsimlab olsin, men natijani "
                    "kuzataman.",
                    "ru": "Пусть команда распределит сама, я слежу за "
                    "результатом.",
                    "en": "Let the team divide it up; I watch the result.",
                },
            ),
            (
                20,
                {
                    "uz": "Eng muhim qismlarni o'zim bajaraman, qolganini "
                    "beraman.",
                    "ru": "Самое важное делаю сам, остальное отдаю.",
                    "en": "Do the important parts myself and delegate the rest.",
                },
            ),
        ],
    },
    {
        "skill": "leadership",
        "text": {
            "uz": "Sizning jamoangiz xato qildi va natija yomon chiqdi. "
            "Rahbaringiz sabab so'rayapti.",
            "ru": "Ваша команда ошиблась, результат плохой. Руководитель "
            "спрашивает, почему.",
            "en": "Your team made a mistake and the result was poor. Your "
            "manager asks why.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Javobgarlikni jamoa nomidan olaman, sababni "
                    "tushuntiraman va tuzatish rejasini aytaman.",
                    "ru": "Беру ответственность за команду, объясняю причину и "
                    "предлагаю план исправления.",
                    "en": "Take responsibility for the team, explain the cause "
                    "and give a plan to fix it.",
                },
            ),
            (
                55,
                {
                    "uz": "Nima bo'lganini xolis tushuntiraman, kim nima "
                    "qilgani bilan.",
                    "ru": "Объясняю объективно, что произошло и кто что делал.",
                    "en": "Explain neutrally what happened and who did what.",
                },
            ),
            (
                25,
                {
                    "uz": "Xato qilgan odam o'zi tushuntirsin deyman.",
                    "ru": "Прошу объяснить того, кто ошибся.",
                    "en": "Ask the person who erred to explain it themselves.",
                },
            ),
            (
                10,
                {
                    "uz": "Sharoit va muddat aybdor ekanini aytaman.",
                    "ru": "Говорю, что виноваты обстоятельства и сроки.",
                    "en": "Say the circumstances and the deadline were to "
                    "blame.",
                },
            ),
        ],
    },
    {
        "skill": "time-management",
        "text": {
            "uz": "Bir kunda uchta ish bor: imtihonga tayyorgarlik, "
            "amaliyotdagi topshiriq va do'stingizga va'da qilgan yordam. "
            "Hammasi ulgurmaydi.",
            "ru": "На один день три дела: подготовка к экзамену, задача на "
            "стажировке и обещанная помощь другу. Всё не успеть.",
            "en": "Three things in one day: exam prep, an internship task, and "
            "help you promised a friend. They will not all fit.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Muddat va oqibatiga qarab tartiblayman, "
                    "do'stimga oldindan aytib, boshqa vaqtga ko'chiraman.",
                    "ru": "Расставляю по срокам и последствиям, другу заранее "
                    "переношу на другое время.",
                    "en": "Rank by deadline and consequence, and tell my "
                    "friend in advance we will move it.",
                },
            ),
            (
                65,
                {
                    "uz": "Eng qiyinidan boshlab, qancha ulgursam shuncha "
                    "qilaman.",
                    "ru": "Начинаю с самого сложного и делаю сколько успею.",
                    "en": "Start with the hardest and do as much as I can.",
                },
            ),
            (
                35,
                {
                    "uz": "Uchalasiga ham ozroqdan vaqt ajrataman.",
                    "ru": "Выделяю на все три понемногу.",
                    "en": "Give each of the three a little time.",
                },
            ),
            (
                15,
                {
                    "uz": "Kechqurun ko'rib chiqaman, hozir birinchisidan "
                    "boshlayman.",
                    "ru": "Разберусь вечером, сейчас начну с первого.",
                    "en": "Sort it out in the evening; start with the first "
                    "for now.",
                },
            ),
        ],
    },
    {
        "skill": "time-management",
        "text": {
            "uz": "Topshiriqni muddatida ulgurmasligingiz ikki kun oldin "
            "ma'lum bo'ldi.",
            "ru": "За два дня до срока стало ясно, что вы не успеваете.",
            "en": "Two days before the deadline it is clear you will not "
            "finish in time.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Darhol ogohlantiraman, yangi realistik muddat va "
                    "tayyor qismini taklif qilaman.",
                    "ru": "Сразу предупреждаю, предлагаю новый реальный срок и "
                    "показываю готовую часть.",
                    "en": "Warn them at once, propose a realistic new date and "
                    "show what is already done.",
                },
            ),
            (
                55,
                {
                    "uz": "Tunlab ishlab, sifatidan biroz yon berib "
                    "ulguraman.",
                    "ru": "Работаю ночами и успеваю, пожертвовав качеством.",
                    "en": "Work nights and make it, at some cost to quality.",
                },
            ),
            (
                35,
                {
                    "uz": "Yordam so'rayman, kimdir qismini olsin.",
                    "ru": "Прошу помощи, чтобы кто-то взял часть.",
                    "en": "Ask for help so someone takes part of it.",
                },
            ),
            (
                10,
                {
                    "uz": "Muddat kuni tushuntiraman — balki ulguraman.",
                    "ru": "Объясню в день срока — вдруг успею.",
                    "en": "Explain on the day; I might still make it.",
                },
            ),
        ],
    },
    {
        "skill": "critical-thinking",
        "text": {
            "uz": "Telegramda \"bu kasb 3 oyda 2000$ beradi\" degan post "
            "ko'rdingiz va u sizga qiziq.",
            "ru": "В Telegram вы увидели пост «эта профессия даёт 2000$ через "
            "3 месяца», и она вам интересна.",
            "en": "You see a Telegram post claiming \"this job pays $2000 "
            "after 3 months\", and the field interests you.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Manbani va real vakansiyalardagi maoshni "
                    "tekshiraman, shu sohada ishlayotgan odamdan so'rayman.",
                    "ru": "Проверяю источник и зарплаты в реальных вакансиях, "
                    "спрашиваю у работающего в этой сфере.",
                    "en": "Check the source and real vacancy salaries, and ask "
                    "someone working in the field.",
                },
            ),
            (
                65,
                {
                    "uz": "Boshqa bir necha manbadan o'qib chiqaman.",
                    "ru": "Читаю ещё несколько источников.",
                    "en": "Read a few other sources.",
                },
            ),
            (
                30,
                {
                    "uz": "Izohlarda odamlar nima deyayotganiga qarayman.",
                    "ru": "Смотрю, что пишут в комментариях.",
                    "en": "See what people say in the comments.",
                },
            ),
            (
                10,
                {
                    "uz": "Ko'pchilik shunday deyayotgan ekan, ishonaman.",
                    "ru": "Раз многие так говорят, верю.",
                    "en": "Plenty of people say it, so I believe it.",
                },
            ),
        ],
    },
    {
        "skill": "critical-thinking",
        "text": {
            "uz": "Hisobotdagi raqam kutilganidan ikki barobar yaxshi "
            "chiqdi. Uni bugun yuborish kerak.",
            "ru": "Показатель в отчёте вдвое лучше ожидаемого. Отчёт нужно "
            "отправить сегодня.",
            "en": "A figure in the report is twice as good as expected. The "
            "report goes out today.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Hisob-kitobni va ma'lumot manbasini qayta "
                    "tekshiraman — yaxshi natija ham xato bo'lishi mumkin.",
                    "ru": "Перепроверяю расчёт и источник данных — хороший "
                    "результат тоже бывает ошибкой.",
                    "en": "Re-check the calculation and the data source — a "
                    "good number can be a wrong number.",
                },
            ),
            (
                70,
                {
                    "uz": "Yuboraman, lekin izohda \"tekshirilishi kerak\" "
                    "deb belgilayman.",
                    "ru": "Отправляю, но помечаю: «требует проверки».",
                    "en": "Send it, flagged as needing verification.",
                },
            ),
            (
                35,
                {
                    "uz": "Hamkasbimdan bir ko'rib berishini so'rayman.",
                    "ru": "Прошу коллегу взглянуть.",
                    "en": "Ask a colleague to glance at it.",
                },
            ),
            (
                10,
                {
                    "uz": "Yaxshi natija — yuboraman.",
                    "ru": "Хороший результат — отправляю.",
                    "en": "Good result — I send it.",
                },
            ),
        ],
    },
    {
        "skill": "problem-solving",
        "text": {
            "uz": "Ishingiz to'xtadi: kerakli dastur ishlamayapti va siz "
            "sababini bilmaysiz.",
            "ru": "Работа встала: нужная программа не работает, причину вы не "
            "знаете.",
            "en": "You are blocked: a tool you need is not working and you do "
            "not know why.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Xatoni aniq yozib olaman, o'zim 20-30 daqiqa "
                    "qidiraman, keyin nima sinaganimni ko'rsatib yordam "
                    "so'rayman.",
                    "ru": "Фиксирую ошибку, ищу сам 20–30 минут, потом прошу "
                    "помощи, показав, что уже пробовал.",
                    "en": "Write the error down, try for 20-30 minutes, then "
                    "ask for help showing what I already tried.",
                },
            ),
            (
                60,
                {
                    "uz": "Yechim topilguncha o'zim qidiraveraman.",
                    "ru": "Ищу сам, пока не найду решение.",
                    "en": "Keep searching on my own until I solve it.",
                },
            ),
            (
                40,
                {
                    "uz": "Darhol biladigan odamdan so'rayman — vaqt ketmasin.",
                    "ru": "Сразу спрашиваю у знающего — чтобы не терять время.",
                    "en": "Ask someone who knows straight away, to save time.",
                },
            ),
            (
                20,
                {
                    "uz": "Boshqa ishga o'taman, keyinroq qaytaman.",
                    "ru": "Переключаюсь на другое, вернусь позже.",
                    "en": "Switch to something else and come back later.",
                },
            ),
        ],
    },
    {
        "skill": "problem-solving",
        "text": {
            "uz": "Bir muammo har hafta takrorlanyapti va har safar qo'lda "
            "tuzatilyapti.",
            "ru": "Одна и та же проблема повторяется каждую неделю и каждый "
            "раз чинится вручную.",
            "en": "The same problem recurs every week and is fixed by hand "
            "each time.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Sababini topib, takrorlanmasligi uchun yechim "
                    "taklif qilaman.",
                    "ru": "Нахожу причину и предлагаю решение, чтобы не "
                    "повторялось.",
                    "en": "Find the cause and propose a fix so it stops "
                    "recurring.",
                },
            ),
            (
                60,
                {
                    "uz": "Tuzatish yo'riqnomasini yozib, jamoaga beraman.",
                    "ru": "Пишу инструкцию по починке и отдаю команде.",
                    "en": "Write the fix up as instructions for the team.",
                },
            ),
            (
                30,
                {
                    "uz": "Rahbarga aytaman, qaror u chiqarsin.",
                    "ru": "Сообщаю руководителю, пусть решает он.",
                    "en": "Tell the manager and let them decide.",
                },
            ),
            (
                10,
                {
                    "uz": "Har safar tuzatib turaman — uzoq vaqt olmaydi.",
                    "ru": "Чиню каждый раз — это недолго.",
                    "en": "Just fix it each time; it does not take long.",
                },
            ),
        ],
    },
    {
        "skill": "presentation",
        "text": {
            "uz": "Ertaga loyihangizni 10 daqiqada tanishmagan auditoriyaga "
            "taqdim qilasiz.",
            "ru": "Завтра вы за 10 минут представляете проект незнакомой "
            "аудитории.",
            "en": "Tomorrow you present your project in 10 minutes to an "
            "audience that does not know you.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Asosiy xabarni bitta jumlaga qisqartiraman, "
                    "vaqtini o'lchab mashq qilaman.",
                    "ru": "Свожу главную мысль к одной фразе и репетирую с "
                    "таймером.",
                    "en": "Reduce the message to one sentence and rehearse "
                    "against a timer.",
                },
            ),
            (
                55,
                {
                    "uz": "Slaydlarni chiroyli va batafsil qilaman.",
                    "ru": "Делаю слайды красивыми и подробными.",
                    "en": "Make the slides detailed and good-looking.",
                },
            ),
            (
                35,
                {
                    "uz": "Matnni yozib olib, o'qib beraman.",
                    "ru": "Пишу текст и читаю его.",
                    "en": "Write the talk out and read it.",
                },
            ),
            (
                20,
                {
                    "uz": "Mavzuni yaxshi bilaman — joyida gapiraveraman.",
                    "ru": "Тему знаю хорошо — расскажу по ходу.",
                    "en": "I know the topic — I will speak off the cuff.",
                },
            ),
        ],
    },
    {
        "skill": "presentation",
        "text": {
            "uz": "Taqdimot o'rtasida javobini bilmaydigan savol berishdi.",
            "ru": "В середине выступления вам задали вопрос, ответа на который "
            "вы не знаете.",
            "en": "Mid-presentation you are asked something you do not know "
            "the answer to.",
        },
        "options": [
            (
                100,
                {
                    "uz": "\"Bilmayman, aniqlab, bugun javob beraman\" deyman "
                    "va haqiqatan yozib olaman.",
                    "ru": "Говорю «не знаю, уточню и отвечу сегодня» — и правда "
                    "записываю вопрос.",
                    "en": "Say \"I do not know, I will check and answer today\" "
                    "— and actually write it down.",
                },
            ),
            (
                60,
                {
                    "uz": "Bilganimni aytib, qolganini keyin aniqlashtiraman.",
                    "ru": "Говорю, что знаю, остальное уточню позже.",
                    "en": "Say what I do know and promise the rest later.",
                },
            ),
            (
                25,
                {
                    "uz": "Savolni mavzuga aloqador emas deb qoldiraman.",
                    "ru": "Отвожу вопрос как не относящийся к теме.",
                    "en": "Set the question aside as off-topic.",
                },
            ),
            (
                5,
                {
                    "uz": "Taxminiy javob beraman — ishonchli eshitilsin.",
                    "ru": "Отвечаю предположением — чтобы звучало уверенно.",
                    "en": "Give a guess so it sounds confident.",
                },
            ),
        ],
    },
    {
        "skill": "adaptability",
        "text": {
            "uz": "Loyiha yarmida talab o'zgardi: qilgan ishingizning katta "
            "qismi endi kerak emas.",
            "ru": "На середине проекта требования изменились: большая часть "
            "сделанного больше не нужна.",
            "en": "Halfway through, the requirements change: much of what you "
            "built is no longer needed.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Nima saqlanib qolishini aniqlab, yangi talabga "
                    "moslashaman va rejani qayta tuzaman.",
                    "ru": "Разбираю, что можно переиспользовать, подстраиваюсь "
                    "и переписываю план.",
                    "en": "Work out what can be reused, adapt, and rewrite the "
                    "plan.",
                },
            ),
            (
                55,
                {
                    "uz": "Yangi talab bo'yicha noldan boshlayman.",
                    "ru": "Начинаю заново по новым требованиям.",
                    "en": "Start again to the new requirements.",
                },
            ),
            (
                30,
                {
                    "uz": "O'zgarish sababini so'rab, qaroni qayta ko'rishni "
                    "taklif qilaman.",
                    "ru": "Спрашиваю причину и предлагаю пересмотреть решение.",
                    "en": "Ask why, and suggest revisiting the decision.",
                },
            ),
            (
                10,
                {
                    "uz": "Boshlagan ishimni tugatib, keyin o'zgartiraman.",
                    "ru": "Доделываю начатое, потом переделываю.",
                    "en": "Finish what I started, then redo it.",
                },
            ),
        ],
    },
    {
        "skill": "adaptability",
        "text": {
            "uz": "Yangi vositani (dastur, uslub) o'rganishingiz kerak, lekin "
            "eski usulingiz ham ishlayapti.",
            "ru": "Нужно освоить новый инструмент, хотя старый способ у вас "
            "работает.",
            "en": "You need to learn a new tool, though your old way still "
            "works.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Kichik vazifada sinab ko'raman va farqini o'zim "
                    "baholayman.",
                    "ru": "Пробую на маленькой задаче и сам оцениваю разницу.",
                    "en": "Try it on a small task and judge the difference "
                    "myself.",
                },
            ),
            (
                70,
                {
                    "uz": "Qo'llanmani o'qib chiqib, keyin qo'llayman.",
                    "ru": "Читаю документацию, потом применяю.",
                    "en": "Read the documentation, then use it.",
                },
            ),
            (
                35,
                {
                    "uz": "Jamoa o'tgandan keyin men ham o'taman.",
                    "ru": "Перейду, когда перейдёт команда.",
                    "en": "Switch when the team switches.",
                },
            ),
            (
                15,
                {
                    "uz": "Eski usulim natija beryapti — o'zgartirmayman.",
                    "ru": "Старый способ даёт результат — не меняю.",
                    "en": "My old way delivers — I keep it.",
                },
            ),
        ],
    },
    {
        "skill": "emotional-intelligence",
        "text": {
            "uz": "Hamkasbingiz ishingizni jamoa oldida keskin tanqid qildi.",
            "ru": "Коллега резко раскритиковал вашу работу при команде.",
            "en": "A colleague criticised your work sharply in front of the "
            "team.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Hozir mazmuniga javob beraman, keyin alohida "
                    "gaplashib, ohangini muhokama qilaman.",
                    "ru": "Сейчас отвечаю по сути, потом наедине обсуждаю тон.",
                    "en": "Answer the substance now, and discuss the tone with "
                    "them privately afterwards.",
                },
            ),
            (
                60,
                {
                    "uz": "Xotirjam javob beraman va mavzuni yopaman.",
                    "ru": "Спокойно отвечаю и закрываю тему.",
                    "en": "Reply calmly and close the subject.",
                },
            ),
            (
                30,
                {
                    "uz": "Jim bo'laman, keyin o'ylab ko'raman.",
                    "ru": "Молчу, потом обдумываю.",
                    "en": "Say nothing and think about it later.",
                },
            ),
            (
                10,
                {
                    "uz": "Xuddi shunday keskin javob qaytaraman.",
                    "ru": "Отвечаю так же резко.",
                    "en": "Answer just as sharply.",
                },
            ),
        ],
    },
    {
        "skill": "emotional-intelligence",
        "text": {
            "uz": "Odatda faol hamkasbingiz bir haftadan beri jim va "
            "charchagan ko'rinadi.",
            "ru": "Обычно активный коллега уже неделю молчалив и выглядит "
            "уставшим.",
            "en": "A normally engaged colleague has been quiet and tired for a "
            "week.",
        },
        "options": [
            (
                100,
                {
                    "uz": "Alohida, bosim o'tkazmasdan holini so'rayman.",
                    "ru": "Спрашиваю наедине, как он, без давления.",
                    "en": "Ask how they are, privately and without pressing.",
                },
            ),
            (
                55,
                {
                    "uz": "Ish yukini yengillashtirishni taklif qilaman.",
                    "ru": "Предлагаю снять часть нагрузки.",
                    "en": "Offer to take some of their load.",
                },
            ),
            (
                30,
                {
                    "uz": "Rahbarga aytaman, u ko'rib chiqsin.",
                    "ru": "Сообщаю руководителю, пусть разберётся.",
                    "en": "Mention it to the manager to look into.",
                },
            ),
            (
                10,
                {
                    "uz": "Shaxsiy ishi — aralashmayman.",
                    "ru": "Это личное — не вмешиваюсь.",
                    "en": "It is personal — I stay out of it.",
                },
            ),
        ],
    },
]


class Command(BaseCommand):
    help = "Seed the platform soft-skill (situational judgement) assessment."

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        author = self._resolve_author()
        if author is None:
            self.stderr.write(
                self.style.ERROR(
                    "No admin user found. Create one with `createsuperuser` first — "
                    "the test needs an author."
                )
            )
            return

        wanted = {row["skill"] for row in SCENARIOS}
        skills = {s.slug: s for s in Skill.objects.filter(slug__in=wanted)}
        missing = wanted - set(skills)
        if missing:
            self.stderr.write(
                self.style.ERROR(
                    f"Missing skills: {', '.join(sorted(missing))}. "
                    "Run `seed_taxonomy` first."
                )
            )
            return

        for language in TITLES:
            self._seed_language(language, author, skills)

        self.stdout.write(
            self.style.SUCCESS(
                f"Soft-skill assessment ready in {len(TITLES)} languages: "
                f"{len(SCENARIOS)} situations across {len(skills)} competencies."
            )
        )

    def _seed_language(self, language: str, author, skills: dict) -> None:
        test, _created = Test.objects.update_or_create(
            type=TestType.SOFT_SKILL,
            employer=None,
            language=language,
            defaults={
                "title": TITLES[language],
                "description": DESCRIPTIONS[language],
                "author": author,
                # No pass mark: a soft-skill profile is a shape, not a verdict.
                "passing_score": 0,
                "time_limit_minutes": 25,
                "max_attempts": 3,
                "shuffle_questions": False,
                "show_correct_answers": True,
                "status": ModerationStatus.PUBLISHED,
                "published_at": timezone.now(),
                "is_public": True,
            },
        )

        for skill in skills.values():
            TestSkill.objects.update_or_create(
                test=test, skill=skill, defaults={"weight": 1.0}
            )

        for order, row in enumerate(SCENARIOS):
            question, _ = Question.objects.update_or_create(
                test=test,
                order=order,
                defaults={
                    "text": row["text"][language],
                    "type": QuestionType.SITUATIONAL,
                    "points": 10,
                    "skill": skills[row["skill"]],
                },
            )
            # Options are rewritten wholesale: matching them individually would
            # need a stable key the content does not have, and a stale option
            # left behind would silently keep scoring.
            question.options.all().delete()
            AnswerOption.objects.bulk_create(
                [
                    AnswerOption(
                        question=question,
                        text=text[language][:500],
                        weight=weight,
                        # Kept meaningful for the "chose a strong response"
                        # statistic; never rendered as right or wrong.
                        is_correct=weight >= 70,
                        order=index,
                    )
                    for index, (weight, text) in enumerate(row["options"])
                ]
            )

    def _resolve_author(self):
        from apps.accounts.models import User
        from apps.common.enums import Role

        return (
            User.objects.filter(role=Role.ADMIN).order_by("date_joined").first()
            or User.objects.filter(is_superuser=True).order_by("date_joined").first()
        )
