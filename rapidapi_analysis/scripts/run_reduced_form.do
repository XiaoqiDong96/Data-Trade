clear all
set more off
cd "/Users/xiaoqidong/Documents/科研/数据交易/rapidapi_analysis"

capture log close
log using "tables/stata_reduced_form.log", text replace

import delimited using "data/api_level.csv", clear varnames(1) bindquote(strict) encoding(UTF-8)
gen ln_subscriptions_s = ln(1 + subscriptions_count)
gen ln_min_paid_price_s = ln(1 + min_paid_price)
gen ln_max_public_quota_s = ln(1 + max_public_quota)
gen ln_public_plan_count_s = ln(1 + public_plan_count)
gen ln_readme_s = ln(1 + readme_len)
gen ln_api_age_s = ln(1 + api_age_days)
gen ln_owner_api_count_s = ln(1 + owner_api_count)
foreach v in ln_min_paid_price_s ln_max_public_quota_s ln_public_plan_count_s ln_readme_s ln_api_age_s ln_owner_api_count_s {
    replace `v' = 0 if missing(`v')
}

reg ln_subscriptions_s has_free_plan ln_min_paid_price_s ln_max_public_quota_s ///
    ln_public_plan_count_s has_soft_limit ln_readme_s ln_api_age_s ln_owner_api_count_s ///
    type_web_scraping type_social_profile type_geo_identity type_firm_lead ///
    type_finance_market type_ecommerce_price type_document_text ///
    type_real_estate_mobility type_public_reference type_freshness, vce(robust)
estimates store demand_baseline

reg ln_min_paid_price_s ln_subscriptions_s ln_max_public_quota_s ///
    ln_public_plan_count_s has_soft_limit ln_readme_s ln_api_age_s ln_owner_api_count_s ///
    type_web_scraping type_social_profile type_geo_identity type_firm_lead ///
    type_finance_market type_ecommerce_price type_document_text ///
    type_real_estate_mobility type_public_reference type_freshness ///
    if min_paid_price < . & min_paid_price > 0, vce(robust)
estimates store price_baseline

import delimited using "data/public_plan_level.csv", clear varnames(1) bindquote(strict) encoding(UTF-8)
gen ln_plan_price_w_s = ln_plan_price_w
gen ln_max_quota_s = ln_max_quota
drop if missing(ln_plan_price_w_s)
gen tier_pro = plan_tier == "PRO"
gen tier_ultra = plan_tier == "ULTRA"
gen tier_mega = plan_tier == "MEGA"
foreach v in ln_max_quota_s has_soft_limit has_positive_overage is_recommended_plan tier_pro tier_ultra tier_mega {
    replace `v' = 0 if missing(`v')
}
encode api_id, gen(api_num)
reghdfe ln_plan_price_w_s ln_max_quota_s has_soft_limit has_positive_overage ///
    is_recommended_plan tier_pro tier_ultra tier_mega, absorb(api_num) vce(cluster api_num)
estimates store plan_within

esttab demand_baseline price_baseline plan_within using "tables/stata_reduced_form_main.csv", ///
    replace se r2 ar2 compress

log close
