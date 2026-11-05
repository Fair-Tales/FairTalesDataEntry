"""Labels and qualifications for the offline alias foundation reports."""


class AliasMetrics:
    report_label = "Exploratory pilot alias foundation diagnostics"
    recall_label = "Headline alias metric: recovery of recorded reference aliases"
    precision_label = "Reference alias precision: diagnostic only"
    precision_reason = (
        "48% (93/195) of pilot books record zero aliases. Reference coverage cannot "
        "be improved by improving the pipeline; suppressing valid extras can raise "
        "this diagnostic without demonstrating better extraction. Do not gate or "
        "rank systems using reference alias precision or F1."
    )
    grounding_label = "M1: lexical grounding in the pipeline's own extracted text"
    grounding_qualification = (
        "This checks lexical support, not independent correctness or attachment. "
        "Missing OCR can leave valid aliases ungrounded; self-consistent wrong OCR "
        "can ground a wrong alias. Empty alias output has undefined grounding."
    )
    input_support_qualification = (
        "Character input is reconstructed from completed pages whose predicted "
        "contains_story flag is true, matching the saved production input policy."
    )
    editorial_qualification = (
        "Editorial classes alone earn no allowance. Typed construction, exact "
        "page-local source spans and matching identity/membership evidence are "
        "required. Structural evidence validation does not establish correct attachment."
    )
    redundancy_label = "M2: within-character Type A candidate pattern redundancy"
    redundancy_qualification = (
        "Historical possessive/subset flags are retained as diagnostics. They do "
        "not authorize removal of raw forms or loss of owner/modifier evidence."
    )
    collision_label = "M3: cross-character Type B candidate collision flags"
    collision_qualification = (
        "Flags remain explicitly unresolved. Shared wording does not establish "
        "either identity or distinctness; incidence is not an identity error rate."
    )
    structural_coverage_qualification = (
        "Incidence denominators contain completed character outputs only. Failed, "
        "partial and unrun books remain in intended coverage and emitted-flag counts; "
        "their missing output is not evidence of zero redundancy or collisions."
    )
    mention_label = "M4: dictionary-uniqueness diagnostic only"
    mention_qualification = (
        "Single-candidate means one dictionary identity, not correct attribution. "
        "True mention recall, correct/wrong attribution, false merges and false "
        "splits remain unmeasured without independent labels."
    )
    report_intro = (
        "Alias recall is the headline alias metric. Reference alias precision and "
        "F1 remain historical diagnostics because 48% of pilot books record no "
        "aliases. M4 is a dictionary-uniqueness diagnostic; candidate uniqueness "
        "does not establish correct attribution."
    )
    conventions = {
        "C1": {"status": "provisional", "default": "Preserve all readable book-authored lettering in archival text; analytical views are separately versioned."},
        "C2": {"status": "provisional", "default": "Main work includes narrative, substantive nonfiction and wordless narrative; exclude covers and supplemental/bibliographic matter."},
        "C3": {"status": "accepted", "default": "Names and descriptive referring expressions are aliases; pronouns and body parts are not lexical aliases. Entity population boundaries remain provisional."},
        "C4": {"status": "accepted", "default": "Photographed edition and first publication use separate scoped fields; this alias-only scorer does not migrate publication data."},
        "C6": {"status": "provisional", "default": "Preserve contributor roles; explicitly credited retellers are included in the analytical authors projection."},
    }
