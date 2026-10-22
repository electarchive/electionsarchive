export const pages = [
  {
    slug: "president-2024",
    title: "2024 presidential election",
    caption:
      "Electoral votes, the popular vote, and every state. The 2020 election is one click away.",
    views: [
      "us/2024-11-05-general/president",
      "us/2024-11-05-general/president?vs=previous",
    ],
    rpc: [],
  },
  {
    slug: "philadelphia-precincts",
    title: "Philadelphia by precinct",
    caption:
      "All 1,664 Philadelphia precincts in 2024. Pennsylvania and Allegheny County are a click away.",
    views: [
      "us/pa/2024-11-05-general/president/philadelphia-county-pa",
      "us/pa/2024-11-05-general/president",
      "us/pa/2024-11-05-general/president/allegheny-county-pa",
    ],
    rpc: [
      [
        "contests/places",
        { id: "01a13afb-2375-7208-8d0f-8e46910f9149", by: "us.county" },
      ],
      [
        "contests/places",
        { id: "01a13afb-232d-7092-8282-761365043854", by: "us.state" },
      ],
    ],
  },
  {
    slug: "texas-swing",
    title: "Texas, 2020 to 2024",
    caption: "How every Texas county moved between 2020 and 2024.",
    views: ["us/tx/2024-11-05-general/president?vs=previous&shade=arrows"],
    rpc: [
      [
        "contests/places",
        { id: "01a13afb-232d-7092-8282-761365043854", by: "us.state" },
      ],
    ],
  },
  {
    slug: "nyc-ranked-choice",
    title: "New York City, round by round",
    caption:
      "The 2025 Democratic mayoral primary, one ranked-choice round at a time.",
    views: [
      "us/ny/new-york-city/2025-06-24-primary/mayor-democratic",
      "us/ny/new-york-city/2025-06-24-primary/mayor-democratic?round=1",
      "us/ny/new-york-city/2025-06-24-primary/mayor-democratic?round=2",
    ],
    rpc: [],
  },
  {
    slug: "ohio-issue-1",
    title: "Ohio Issue 1, 2023",
    caption:
      "A ballot measure with its official wording and the vote in all 88 counties.",
    views: ["us/oh/2023-11-07-general/ohio-issue-1"],
    rpc: [],
  },
  {
    slug: "illinois-1860",
    title: "Illinois, 1860",
    caption:
      "Lincoln's home state, county by county, on the county lines of 1860.",
    views: ["us/il/1860-11-06-general/president"],
    rpc: [
      [
        "contests/places",
        { id: "1f2a77ac-1df9-5d74-956f-ff368a3e92e0", by: "us.state" },
      ],
    ],
  },
  {
    slug: "us-2024-general",
    title: "2024 general election",
    caption:
      "President, House, and Senate on one page, with every chamber seat by seat.",
    views: ["us/2024-11-05-general"],
    rpc: [
      ["bodies/get", { id: "01a13aad-ae18-70cb-af59-d7ab721a38a4" }],
      ["bodies/get", { id: "01a13aad-ae18-70cb-af59-d7aac66c0259" }],
      ["contests/locate", { path: "us/2024-11-05-general/president" }],
      [
        "elections/regional",
        {
          id: "01a13afb-232d-7092-8282-76126951f01f",
          contest: "3b45e238-f246-5a77-8afb-24b55d807206",
        },
      ],
      [
        "elections/regional",
        {
          id: "01a13afb-232d-7092-8282-76126951f01f",
          contest: "01a13b5c-4907-7533-8d26-48d1c8263f59",
        },
      ],
      [
        "contests/get",
        {
          id: "01a13afb-232d-7092-8282-761365043854",
          vs: "previous",
          by: "us.state",
        },
      ],
    ],
  },
  {
    slug: "democratic-party",
    title: "Democratic Party",
    caption: "Two centuries of Democratic Party votes and seats.",
    views: ["parties/us/democratic-party"],
    rpc: [["elections/get", { id: "01a13afb-232d-7092-8282-76126951f01f" }]],
  },
  {
    slug: "bernie-sanders",
    title: "Bernie Sanders",
    caption:
      "Fifty years of races under three party labels, from Liberty Union to the Senate.",
    views: ["people/bernie-sanders"],
    rpc: [],
  },
  {
    slug: "uk-2024",
    title: "UK general election, 2024",
    caption: "650 constituencies and the House of Commons they elected.",
    views: ["gb/2024-07-04-general"],
    rpc: [
      [
        "elections/regional",
        {
          id: "958cdc48-4dd5-5f45-ada7-529311c37bd8",
          contest: "eec5d6ff-3ee9-581a-a597-12b8f6a26715",
        },
      ],
      ["bodies/get", { id: "01a13b8d-56e9-747a-a0a6-f9741f70db7e" }],
    ],
  },
];
