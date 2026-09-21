/** Curated official-source context, separate from live oracle data. Author: Carter Tan.
 * Exact ETH address identifiers verified in OFAC SDN.XML published 2026-09-18,
 * retrieved 2026-09-21. Entity dates come from linked Treasury notices.
 * This is a dated snapshot for three demo wallets, not a live OFAC integration.
 */
const common={authority:'U.S. Treasury / OFAC',list:'SDN',snapshotPublished:'2026-09-18',verifiedOn:'2026-09-21',source:'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML',addressListingDate:null};
export const CONTEXT={
'0xfda1ec4a6178d4916b001a065422d31ebe5f62ff':{...common,entity:'SIM, Hyon Sop',uid:'42498',program:'NPWMD',entityDesignationDate:'2023-04-24',reason:'Treasury states that Sim was designated under E.O. 13382 for representing Korea Kwangson Banking Corp, a previously designated bank.',notice:'https://home.treasury.gov/news/press-releases/jy1435'},
'0xcb74874f1e06fcf80a306e06e5379a44b488ba2d':{...common,entity:'AMNOKGANG TECHNOLOGY DEVELOPMENT COMPANY',uid:'57056',program:'DPRK4',entityDesignationDate:'2026-03-12',reason:'Treasury states that Amnokgang was designated under E.O. 13810 for operating in North Korea’s IT industry. The notice describes overseas IT-worker operations and illicit procurement.',notice:'https://home.treasury.gov/news/press-releases/sb0416'},
'0x95584c303fcd48af5c6b9873015f2ad0ca84eae3':{...common,entity:'YUN, Song Guk',uid:'57034',program:'DPRK4',entityDesignationDate:'2026-03-12',reason:'Treasury states that Yun was designated under E.O. 13810 as a North Korean person engaged in commercial activity generating government revenue. The notice describes his leadership of an overseas IT-worker group.',notice:'https://home.treasury.gov/news/press-releases/sb0416'}
};
