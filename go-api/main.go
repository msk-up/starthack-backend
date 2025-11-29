package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime"
	"github.com/gin-gonic/gin"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/joho/godotenv"
)

var (
	pool          *pgxpool.Pool
	bedrockClient *bedrockruntime.Client
)

// Models

type Supplier struct {
	SupplierID  string  `json:"supplier_id"`
	Description string  `json:"description"`
	Insights    *string `json:"insights"`
	ImageURL    *string `json:"image_url"`
}

type Product struct {
	ProductID   string `json:"product_id"`
	SupplierID  string `json:"supplier_id"`
	ProductName string `json:"product_name"`
}

type NegotiationRequest struct {
	Product   int      `json:"product"`
	Prompt    string   `json:"prompt"`
	Tactics   string   `json:"tactics"`
	Suppliers []string `json:"suppliers"`
}

// Bedrock request/response types

type BedrockMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type BedrockRequest struct {
	Messages    []BedrockMessage `json:"messages"`
	MaxTokens   int              `json:"max_tokens"`
	Temperature float64          `json:"temperature"`
}

type BedrockChoice struct {
	Message BedrockMessage `json:"message"`
}

type BedrockResponse struct {
	Choices []BedrockChoice `json:"choices"`
}

func callBedrock(prompt string, systemPrompt string) (string, error) {
	messages := []BedrockMessage{}

	if systemPrompt != "" {
		messages = append(messages, BedrockMessage{Role: "system", Content: systemPrompt})
	}
	messages = append(messages, BedrockMessage{Role: "user", Content: prompt})

	reqBody := BedrockRequest{
		Messages:    messages,
		MaxTokens:   1024,
		Temperature: 0.7,
	}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return "", err
	}

	resp, err := bedrockClient.InvokeModel(context.TODO(), &bedrockruntime.InvokeModelInput{
		ModelId:     aws.String("openai.gpt-oss-120b-1:0"),
		ContentType: aws.String("application/json"),
		Accept:      aws.String("application/json"),
		Body:        bodyBytes,
	})
	if err != nil {
		return "", err
	}

	var bedrockResp BedrockResponse
	if err := json.Unmarshal(resp.Body, &bedrockResp); err != nil {
		return "", err
	}

	if len(bedrockResp.Choices) == 0 {
		return "", fmt.Errorf("no choices in response")
	}

	return bedrockResp.Choices[0].Message.Content, nil
}

// Handlers

func healthHandler(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func listSuppliersHandler(c *gin.Context) {
	rows, err := pool.Query(context.Background(), "SELECT supplier_id, description, insights, image_url FROM supplier")
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var suppliers []Supplier
	for rows.Next() {
		var s Supplier
		if err := rows.Scan(&s.SupplierID, &s.Description, &s.Insights, &s.ImageURL); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
		suppliers = append(suppliers, s)
	}

	c.JSON(http.StatusOK, suppliers)
}

func listProductsHandler(c *gin.Context) {
	rows, err := pool.Query(context.Background(), "SELECT product_id, supplier_id, product_name FROM product")
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var products []Product
	for rows.Next() {
		var p Product
		if err := rows.Scan(&p.ProductID, &p.SupplierID, &p.ProductName); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
		products = append(products, p)
	}

	c.JSON(http.StatusOK, products)
}

func searchHandler(c *gin.Context) {
	product := c.Query("product")
	if product == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "product query param required"})
		return
	}

	rows, err := pool.Query(context.Background(),
		"SELECT product_id, supplier_id, product_name FROM product WHERE product_name ILIKE $1",
		"%"+product+"%")
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()

	var products []Product
	for rows.Next() {
		var p Product
		if err := rows.Scan(&p.ProductID, &p.SupplierID, &p.ProductName); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
		products = append(products, p)
	}

	c.JSON(http.StatusOK, products)
}

func testBedrockHandler(c *gin.Context) {
	prompt := "Explain the benefits of using Amazon Bedrock for AI applications."
	response, err := callBedrock(prompt, "")
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"response": response})
}

func negotiationsHandler(c *gin.Context) {
	var req NegotiationRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// TODO: implement negotiations
	c.JSON(http.StatusOK, gin.H{"status": "not implemented"})
}

func main() {
	// Load .env from parent directory
	if err := godotenv.Load("../.env"); err != nil {
		log.Println("Warning: .env file not found")
	}

	dbURL := os.Getenv("DB_URL")
	if dbURL == "" {
		log.Fatal("DB_URL environment variable required")
	}

	// Initialize DB pool
	var err error
	pool, err = pgxpool.New(context.Background(), dbURL)
	if err != nil {
		log.Fatalf("Unable to connect to database: %v", err)
	}
	defer pool.Close()

	// Initialize Bedrock client
	awsRegion := os.Getenv("AWS_REGION")
	if awsRegion == "" {
		awsRegion = "eu-west-1"
	}

	cfg, err := config.LoadDefaultConfig(context.TODO(), config.WithRegion(awsRegion))
	if err != nil {
		log.Fatalf("Unable to load AWS config: %v", err)
	}
	bedrockClient = bedrockruntime.NewFromConfig(cfg)

	// Setup router
	r := gin.Default()
	r.SetTrustedProxies(nil)
	r.GET("/health", healthHandler)
	r.GET("/suppliers", listSuppliersHandler)
	r.GET("/products", listProductsHandler)
	r.GET("/search", searchHandler)
	r.GET("/test", testBedrockHandler)
	r.POST("/negotiations", negotiationsHandler)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8000"
	}

	log.Printf("Starting server on port %s", port)
	if err := r.Run(":" + port); err != nil {
		log.Fatalf("Failed to start server: %v", err)
	}
}
