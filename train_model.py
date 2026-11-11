"""Train the existing TF-IDF + Logistic Regression grievance classifier."""
from pathlib import Path
import argparse, json
import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

ROOT = Path(__file__).resolve().parent
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT/"data"/"complaints_dataset.xlsx")
    args = parser.parse_args()
    df = pd.read_excel(args.data)
    cols = {str(c).strip().lower(): c for c in df.columns}
    text_col = next((cols[k] for k in ("complaint text","complaint","text") if k in cols), None)
    label_col = next((cols[k] for k in ("category","department","label") if k in cols), None)
    if not text_col or not label_col:
        raise ValueError("Dataset needs complaint text and category columns.")
    data = df[[text_col,label_col]].dropna().copy()
    data.columns = ["text","label"]
    data["text"] = data["text"].astype(str).str.strip()
    data["label"] = data["label"].astype(str).str.strip()
    data = data[(data.text!="") & (data.label!="")]
    X_train,X_test,y_train,y_test = train_test_split(data.text,data.label,test_size=.2,random_state=42,stratify=data.label)
    model = Pipeline([("tfidf",TfidfVectorizer(ngram_range=(1,2),max_features=30000,sublinear_tf=True)),
                      ("classifier",LogisticRegression(max_iter=2000,class_weight="balanced",random_state=42))])
    model.fit(X_train,y_train)
    pred=model.predict(X_test)
    out=ROOT/"models"; out.mkdir(exist_ok=True)
    joblib.dump(model,out/"grievance_pipeline.joblib")
    metrics={"accuracy":accuracy_score(y_test,pred),"report":classification_report(y_test,pred,output_dict=True,zero_division=0),
             "labels":sorted(data.label.unique().tolist()),"confusion_matrix":confusion_matrix(y_test,pred,labels=sorted(data.label.unique())).tolist(),
             "train_rows":len(X_train),"test_rows":len(X_test),"random_state":42}
    (out/"evaluation.json").write_text(json.dumps(metrics,indent=2),encoding="utf-8")
    print(f"Model saved to {out/'grievance_pipeline.joblib'}")
    print(f"Test accuracy: {metrics['accuracy']:.3f}")
    print(classification_report(y_test,pred,zero_division=0))
if __name__=="__main__": main()
