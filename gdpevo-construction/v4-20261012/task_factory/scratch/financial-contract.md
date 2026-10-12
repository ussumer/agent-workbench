# Financial semantics contract (construction side only)

The environment supplies dated contractual facts and raw source records. This document is not staged to Actor. Any unresolved value is a failed data/design condition, not permission to invent gold. E004/E005 source ambiguities and synthetic extensions are disclosed in adaptation.md.

1. UOM explicitly distinguishes vehicles, headlamp pieces and vehicle_sets (one set two headlamps). Convert quotedUOM before comparisons. Tiers define closed/open bounds, measurementUOM,cumulativecounter andmarginal/allunits behavior in businessdocuments. No guessingthreshold equality.
2. Resolve effective businessrecords by dated approval/effectivity/amendment evidence, not maximumrevision. Futureapproved/draft/revoked/superseded sources must be distinguishable; accessible sources must permit deterministic resolution.
3. Tooling quote amounts repeated acrossvariants are a commonprogramexposure,not separatevariantcharges. Contractspecifies charge/recovery form andcap; separate tooling allocationfromcashpayment toavoiddoublecount. No generalrule inventedinchecker withoutbusiness/trainevidence.
4. Nomination NPV referenceframe sourceE004: year1undiscounted,years2-4discountedat10%;upfrontR&D atvaluationdate,split equally amongvariants. Generatedcalendar/paymenttiming mustagree. Additionalfinanceframesrequireexplicit generatedterms and recurrenttrainanchors.
5. Replacement refundfraction.30 applies toactualpaideligibletooling+R&D amount undergeneratedcontract. Depositfraction,paymentsmade,claimablefraction,creditreceived anddueclaim areseparate;no applying.30twice. This is declared synthetic interpretationofambiguousE005,notattributedasuniqueoriginalmeaning.
6. Historical paidexposure,refundclaims,receivedcredits,reusableownedtooling,newtooling/R&D andtransitioncostsare separate. Unreceivedclaim is notcashreceipt. Futureincrementalcomparison excludessunkexposure exceptrequested netliabilityoutputs.
7. Engineeringqualification,sourceeffective dates,capacityandlaunchfeasibilitymustderivefromindependentrecords;no eligible-for-task,winner,expectedcost fields. Ranking andrecommendationmay beoutputs butcannottriple-score samecostrootdecision.
8. FX numericconsequencesonlywhereactualcurrency clausesanddatedrates/scenarios areprovided. E005qualitativerisk alone doesnot justifyinventingnumericFXrisk.
9. Gold/calculations useDecimal or integer cents,declared endrounding/tolerance,currency/UOM persistedinoutputs. Legitimateties allowed. Arithmetic/controlplane reference canrunhost;Actor computationsonly realOpenSandbox.

## Scoped review resolutions
Knowledge cutoff uses issued/approved_at<=asof2026-10-12; projection fiscalyears2027-2030 may naturally befuture. Forecastsarefullfiscal-yearquantities,notH2records;generatorwillmarkquantity_scope andannualcoverage. Neverblindlyreject2027sourceapplicabilitybecausecurrentasof2026. Quoteamendmentsreplaceearliercharge terms,notcreateadditionaltooling/RDliabilities;sharedIDs andsupersedeslinksdocumentthis.
N2 onlycheckscommercialcoverage/applicability;N6ownsallpricecurrency/UOMarithmetic. Nomination/refund/CAP dimensionsmustnotbeconstantcopyvalues. Selectedreplacementtrain005PRG040/test004PRG110areactualgeneratedpartialrefundbusinesscases,notmanualgoldlabels.
