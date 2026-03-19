#!/bin/bash
export FILTER_BRANCH_SQUELCH_WARNING=1
git filter-branch --force --index-filter 'git rm --cached --ignore-unmatch "ibtfiles/cadillac/cadillacvseriesrgtp_silverstone 2019 gp 2026-03-17 19-58-55.ibt"' --prune-empty HEAD
